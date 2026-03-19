import os
import shutil
import socket
import subprocess
import threading
import time

from flask import (
    current_app, render_template, abort, request, url_for,
    jsonify, send_from_directory, after_this_request, Response,
)
from flask_login import login_required, current_user
from flask_sock import Sock

from app.extensions import csrf, db
from app.models import AccessPoint, Resource, can_user_access
from app.console import bp

sock = Sock()

DRIVE_MAX_AGE_DAYS = 7


def _drive_base():
    """Return the base drive directory path."""
    return os.path.realpath(current_app.config.get('DRIVE_PATH', os.path.join(
        current_app.root_path, '..', 'data', 'drive')))


def _resource_drive_path(resource_id):
    """Return the absolute path to a resource's drive directory."""
    return os.path.join(_drive_base(), str(resource_id))


def _ensure_drive_dir(resource_id):
    """Ensure the resource drive directory exists and is accessible.

    Handles the case where the base drive dir was created by Docker (root)
    and may not be writable.  Returns the path or None on failure.
    """
    base = _drive_base()
    drive = os.path.join(base, str(resource_id))

    # If drive already exists and is accessible, short-circuit
    if os.path.isdir(drive) and os.access(drive, os.R_OK | os.W_OK):
        return drive

    # Fix base dir permissions if it exists but isn't writable
    if os.path.isdir(base) and not os.access(base, os.W_OK):
        try:
            subprocess.run(['chmod', 'a+rwx', base], check=True,
                           capture_output=True, timeout=5)
        except Exception:
            pass

    # Create the resource subdirectory
    try:
        os.makedirs(drive, mode=0o777, exist_ok=True)
    except OSError:
        pass

    # Fix permissions on the resource dir (may have been created by guacd)
    if os.path.isdir(drive) and not os.access(drive, os.R_OK | os.W_OK):
        try:
            subprocess.run(['chmod', '-R', 'a+rwX', drive], check=True,
                           capture_output=True, timeout=10)
        except Exception:
            pass

    return drive if os.path.isdir(drive) else None


def _fix_file_permissions(filepath):
    """Make a single file readable+writable (created by guacd as root)."""
    try:
        subprocess.run(['chmod', 'a+rw', filepath], check=True,
                       capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def _check_resource_access(resource_id):
    """Verify user has access to the resource. Returns Resource or aborts."""
    resource = db.session.get(Resource, resource_id)
    if not resource:
        abort(404)
    if not can_user_access(current_user, resource):
        abort(403)
    return resource


@bp.route('/<int:resource_id>/files')
@login_required
def list_files(resource_id):
    """List files in a resource's drive directory."""
    _check_resource_access(resource_id)
    drive = _ensure_drive_dir(resource_id)
    if not drive or not os.path.isdir(drive):
        return jsonify([])
    try:
        entries = os.listdir(drive)
    except PermissionError:
        return jsonify({'error': 'Permission denied on drive directory.'}), 500
    files = []
    for name in sorted(entries):
        path = os.path.join(drive, name)
        if os.path.isfile(path):
            try:
                files.append({
                    'name': name,
                    'size': os.path.getsize(path),
                })
            except PermissionError:
                files.append({'name': name, 'size': 0})
    return jsonify(files)


@bp.route('/<int:resource_id>/files/<path:filename>')
@login_required
def download_file(resource_id, filename):
    """Download a file from a resource's drive directory, then delete it."""
    _check_resource_access(resource_id)
    drive = _ensure_drive_dir(resource_id)
    if not drive:
        abort(404)
    safe = os.path.realpath(os.path.join(drive, filename))
    if not safe.startswith(drive + os.sep) and safe != drive:
        abort(403)
    if not os.path.isfile(safe):
        abort(404)
    if not os.access(safe, os.R_OK):
        _fix_file_permissions(safe)

    # Auto-delete file after it has been sent to the client
    file_to_delete = safe

    @after_this_request
    def _cleanup(response: Response) -> Response:
        try:
            os.remove(file_to_delete)
        except OSError:
            pass
        return response

    return send_from_directory(drive, filename, as_attachment=True)


@bp.route('/<int:resource_id>/files/<path:filename>', methods=['DELETE'])
@csrf.exempt
@login_required
def delete_file(resource_id, filename):
    """Delete a file from a resource's drive directory."""
    _check_resource_access(resource_id)
    drive = _ensure_drive_dir(resource_id)
    if not drive:
        abort(404)
    safe = os.path.realpath(os.path.join(drive, filename))
    if not safe.startswith(drive + os.sep) and safe != drive:
        abort(403)
    if not os.path.isfile(safe):
        abort(404)
    if not os.access(safe, os.W_OK):
        _fix_file_permissions(safe)
    os.remove(safe)
    return '', 204


def purge_old_drive_files(app):
    """Delete drive files older than DRIVE_MAX_AGE_DAYS. Called by scheduler."""
    with app.app_context():
        base = os.path.realpath(app.config.get('DRIVE_PATH', os.path.join(
            app.root_path, '..', 'data', 'drive')))
        if not os.path.isdir(base):
            return
        cutoff = time.time() - (DRIVE_MAX_AGE_DAYS * 86400)
        removed = 0
        for resource_dir in os.scandir(base):
            if not resource_dir.is_dir():
                continue
            for dirpath, _dirs, files in os.walk(resource_dir.path):
                for fname in files:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        if os.path.getmtime(fpath) < cutoff:
                            os.remove(fpath)
                            removed += 1
                    except OSError:
                        pass
        if removed:
            app.logger.info(f'Drive purge: removed {removed} files older than {DRIVE_MAX_AGE_DAYS} days')


def init_sock(app):
    """Attach flask-sock to the app (called from app factory)."""
    sock.init_app(app)


def _ws_close(ws, message=None):
    """Close WebSocket and shut down the underlying socket.

    After flask-sock's route handler returns, Werkzeug writes an HTTP 200
    response on the same socket.  If the socket is still open, those bytes
    corrupt the WebSocket stream and the browser reports
    "Invalid frame header".  Shutting down the raw socket here forces
    Werkzeug's write to fail with ConnectionError, which it handles
    gracefully.
    """
    try:
        ws.close(message=message)
    except Exception:
        pass
    try:
        ws.sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        ws.sock.close()
    except Exception:
        pass


@bp.route('/<int:ap_id>')
@login_required
def session(ap_id):
    """Render the in-browser console page for an access point."""
    ap = db.session.get(AccessPoint, ap_id) or abort(404)
    if not ap.is_enabled:
        abort(404)
    resource = db.session.get(Resource, ap.resource_id) or abort(404)

    if not can_user_access(current_user, resource):
        abort(403)

    tunnel_url = url_for('console.tunnel', ap_id=ap_id)

    return render_template(
        'console/session.html',
        ap=ap,
        resource=resource,
        tunnel_path=tunnel_url,
    )


def _encode_instruction(opcode, args):
    """Encode a Guacamole protocol instruction."""
    parts = [opcode] + list(args)
    return ','.join(f'{len(str(p))}.{p}' for p in parts) + ';'


def _parse_instruction(data):
    """Parse a single Guacamole instruction from text.
    Returns (opcode, args_list) or None if incomplete/invalid.
    """
    idx = 0
    elements = []
    while idx < len(data):
        dot = data.find('.', idx)
        if dot < 0:
            return None
        try:
            length = int(data[idx:dot])
        except ValueError:
            return None
        value_start = dot + 1
        value_end = value_start + length
        if value_end > len(data):
            return None
        elements.append(data[value_start:value_end])
        if value_end >= len(data):
            return None
        separator = data[value_end]
        idx = value_end + 1
        if separator == ';':
            if not elements:
                return None
            return (elements[0], elements[1:])
        elif separator != ',':
            return None
    return None


@bp.route('/diagnostics')
@login_required
def diagnostics():
    """Check guacd connectivity and report status."""
    import json

    guacd_host = current_app.config.get('GUACD_HOST', 'localhost')
    guacd_port = current_app.config.get('GUACD_PORT', 4822)

    result = {
        'guacd_host': guacd_host,
        'guacd_port': guacd_port,
        'guacd_reachable': False,
        'guacd_version': None,
        'error': None,
    }

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((guacd_host, guacd_port))
        result['guacd_reachable'] = True

        # Send a select to see if guacd responds
        s.sendall(_encode_instruction('select', ['rdp']).encode('utf-8'))
        buf = b''
        while b';' not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        if buf:
            parsed = _parse_instruction(buf.decode('utf-8', errors='replace'))
            if parsed:
                result['guacd_response'] = parsed[0]
                result['guacd_args'] = parsed[1][:5]  # first 5 args
        s.close()
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'

    return json.dumps(result, indent=2), 200, {'Content-Type': 'application/json'}


@sock.route('/<int:ap_id>/tunnel', bp=bp)
def tunnel(ws, ap_id):
    """WebSocket <-> guacd TCP relay."""
    log = current_app.logger

    log.info(f'[tunnel:{ap_id}] WebSocket opened, user authenticated: '
             f'{current_user.is_authenticated}')

    if not current_user.is_authenticated:
        log.warning(f'[tunnel:{ap_id}] Rejected: not authenticated')
        _ws_close(ws, 'Not authenticated')
        return

    ap = db.session.get(AccessPoint, ap_id)
    if not ap or not ap.is_enabled:
        log.warning(f'[tunnel:{ap_id}] Rejected: access point not found or disabled')
        _ws_close(ws, 'Access point not found')
        return

    resource = db.session.get(Resource, ap.resource_id)
    if not resource or not can_user_access(current_user, resource):
        log.warning(f'[tunnel:{ap_id}] Rejected: access denied for user {current_user.id}')
        _ws_close(ws, 'Access denied')
        return

    guacd_host = current_app.config.get('GUACD_HOST', 'localhost')
    guacd_port = current_app.config.get('GUACD_PORT', 4822)

    log.info(f'[tunnel:{ap_id}] AP: protocol={ap.protocol} '
             f'host={ap.hostname}:{ap.effective_port} '
             f'user={ap.username or "(none)"}')
    log.info(f'[tunnel:{ap_id}] Connecting to guacd at {guacd_host}:{guacd_port}')

    # Open raw TCP connection to guacd
    guacd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    guacd_sock.settimeout(20)
    try:
        guacd_sock.connect((guacd_host, guacd_port))
    except Exception as e:
        log.error(f'[tunnel:{ap_id}] Cannot connect to guacd at '
                  f'{guacd_host}:{guacd_port}: {e}')
        _ws_close(ws, 'Cannot reach guacd')
        return

    log.info(f'[tunnel:{ap_id}] Connected to guacd')

    # Connection parameters to inject during handshake
    connect_params = {
        'hostname': ap.hostname,
        'port': str(ap.effective_port),
        'username': ap.username or '',
        'password': ap.password or '',
    }
    # Pre-create resource drive directory with correct permissions so Flask
    # can read files later.  guacd maps ./data/drive → /drive inside the
    # container, so the container path is /drive/<resource_id>.
    resource_id = resource.id
    container_drive = f'/drive/{resource_id}'
    _ensure_drive_dir(resource_id)

    if ap.protocol == 'rdp':
        connect_params.update({
            'security': 'any',
            'ignore-cert': 'true',
            'enable-font-smoothing': 'true',
            'enable-drive': 'true',
            'drive-path': container_drive,
            'drive-name': 'Shared',
            'create-drive-path': 'true',
            # Performance: disable heavy Windows desktop effects
            'disable-wallpaper': 'true',
            'disable-theming': 'true',
            'disable-full-window-drag': 'true',
            'disable-menu-animations': 'true',
            'disable-bitmap-caching': 'false',
            'resize-method': 'display-update',
        })
    elif ap.protocol == 'ssh':
        connect_params.update({
            'color-scheme': 'gray-black',
            'font-size': '14',
            'terminal-type': 'xterm-256color',
            'enable-sftp': 'true',
            'sftp-root-directory': container_drive,
        })

    # Update access tracking
    from datetime import datetime, timezone
    ap.last_accessed_by = current_user.id
    ap.last_accessed_at = datetime.now(timezone.utc)
    db.session.commit()

    # --- Phase 1: guacd handshake ---

    try:
        guacd_sock.setblocking(True)
        guacd_sock.settimeout(10)

        # Step 1: Send 'select'
        instruction = _encode_instruction('select', [ap.protocol])
        log.info(f'[tunnel:{ap_id}] -> guacd: {instruction.strip()}')
        guacd_sock.sendall(instruction.encode('utf-8'))

        # Step 2: Receive 'args'
        buf = b''
        while b';' not in buf:
            chunk = guacd_sock.recv(4096)
            if not chunk:
                raise ConnectionError('guacd closed during handshake')
            buf += chunk

        args_text = buf.decode('utf-8')
        log.info(f'[tunnel:{ap_id}] <- guacd: {args_text[:200]}')
        parsed = _parse_instruction(args_text)
        if not parsed or parsed[0] != 'args':
            raise ConnectionError(f'Expected args from guacd, got: {args_text[:100]}')
        args_list = parsed[1]

        # Step 3: Send size, audio, video, image, connect
        # Use browser's actual dimensions if provided, else default
        client_width = request.args.get('width', '1920')
        client_height = request.args.get('height', '1080')
        client_dpi = request.args.get('dpi', '96')
        log.info(f'[tunnel:{ap_id}] Client size: {client_width}x{client_height} @ {client_dpi}dpi')
        for instr_name, instr_args in [
            ('size', [client_width, client_height, client_dpi]),
            ('audio', ['audio/L16']),
            ('video', []),
            ('image', []),
        ]:
            instr = _encode_instruction(instr_name, instr_args)
            log.debug(f'[tunnel:{ap_id}] -> guacd: {instr.strip()}')
            guacd_sock.sendall(instr.encode('utf-8'))

        # Build connect args matching the args list from guacd
        connect_args = []
        for arg_name in args_list:
            connect_args.append(connect_params.get(arg_name, ''))
        connect_instr = _encode_instruction('connect', connect_args)
        # Log connect without password
        safe_args = [f'{k}={v}' for k, v in zip(args_list, connect_args)
                     if k != 'password']
        log.info(f'[tunnel:{ap_id}] -> guacd: connect({", ".join(safe_args)})')
        guacd_sock.sendall(connect_instr.encode('utf-8'))

        # Step 4: Receive 'ready'
        buf = b''
        while b';' not in buf:
            chunk = guacd_sock.recv(4096)
            if not chunk:
                raise ConnectionError('guacd closed during handshake')
            buf += chunk

        ready_text = buf.decode('utf-8')
        log.info(f'[tunnel:{ap_id}] <- guacd: {ready_text[:200]}')
        parsed = _parse_instruction(ready_text)
        if not parsed or parsed[0] != 'ready':
            raise ConnectionError(
                f'Expected ready from guacd, got: {ready_text[:100]}')

        connection_id = parsed[1][0] if parsed[1] else '?'
        log.info(f'[tunnel:{ap_id}] Handshake complete, connection_id={connection_id}')

    except Exception as e:
        log.error(f'[tunnel:{ap_id}] guacd handshake failed: {e}')
        guacd_sock.close()
        _ws_close(ws, 'guacd handshake failed')
        return

    # --- Phase 2: Threaded relay ---
    #
    # Two threads, one per direction, so neither side blocks the other.
    #
    # guacd→browser: blocking recv on TCP, buffer until instruction
    #   boundary (';'), then send complete instructions over WebSocket.
    # browser→guacd: blocking WebSocket receive, forward to TCP.
    #
    # When either side disconnects, the thread exits and signals the
    # other via shutdown().

    guacd_sock.setblocking(True)
    guacd_sock.settimeout(None)
    done = threading.Event()

    def guacd_to_browser():
        """Forward guacd TCP data to browser WebSocket."""
        buf = b''
        try:
            while not done.is_set():
                chunk = guacd_sock.recv(65536)
                if not chunk:
                    log.info(f'[tunnel:{ap_id}] guacd disconnected')
                    break
                buf += chunk
                # Only send complete instructions (up to last ';')
                last_semi = buf.rfind(b';')
                if last_semi >= 0:
                    to_send = buf[:last_semi + 1]
                    buf = buf[last_semi + 1:]
                    ws.send(to_send.decode('utf-8', errors='replace'))
        except Exception as e:
            if not done.is_set():
                log.debug(f'[tunnel:{ap_id}] guacd→browser ended: '
                          f'{type(e).__name__}: {e}')
        finally:
            done.set()
            try:
                _ws_close(ws)
            except Exception:
                pass

    def browser_to_guacd():
        """Forward browser WebSocket data to guacd TCP."""
        try:
            while not done.is_set():
                data = ws.receive()
                if data is None:
                    log.info(f'[tunnel:{ap_id}] Browser disconnected')
                    break
                raw = data.encode('utf-8') if isinstance(data, str) else data
                guacd_sock.sendall(raw)
        except Exception as e:
            if not done.is_set():
                log.debug(f'[tunnel:{ap_id}] browser→guacd ended: '
                          f'{type(e).__name__}: {e}')
        finally:
            done.set()
            try:
                guacd_sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass

    log.info(f'[tunnel:{ap_id}] Starting relay threads')
    t_g2b = threading.Thread(target=guacd_to_browser, daemon=True)
    t_b2g = threading.Thread(target=browser_to_guacd, daemon=True)
    t_g2b.start()
    t_b2g.start()

    # Wait for both threads to finish
    t_g2b.join()
    t_b2g.join()

    log.info(f'[tunnel:{ap_id}] Tunnel closed')
    try:
        guacd_sock.close()
    except Exception:
        pass
    _ws_close(ws)
