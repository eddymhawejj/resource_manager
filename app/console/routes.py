import socket
import threading

from flask import (
    current_app, render_template, abort, url_for,
)
from flask_login import login_required, current_user
from flask_sock import Sock

from app.extensions import db
from app.models import AccessPoint, Resource, can_user_access
from app.console import bp

sock = Sock()


def init_sock(app):
    """Attach flask-sock to the app (called from app factory)."""
    sock.init_app(app)


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
    ws_tunnel_url = tunnel_url  # JS builds full ws:// URL from location

    return render_template(
        'console/session.html',
        ap=ap,
        resource=resource,
        tunnel_path=ws_tunnel_url,
    )


def _build_guacd_select(ap):
    """Build the initial 'select' instruction the browser should send.

    The Guacamole.Client JS sends 'select,<protocol>' as the first
    instruction over the tunnel.  We intercept it to inject our connection
    parameters into the 'connect' instruction later.  But actually the
    cleanest approach is to let the JS drive the whole protocol and just
    relay bytes.

    However Guacamole.Client.connect(data) just opens the WebSocket — the
    actual protocol negotiation (select/args/size/audio/video/image/connect)
    is handled by the JS client talking through the tunnel to guacd.

    Since the JS client needs to know what protocol to select and what
    connection args to provide, we encode that in the connect data string
    that gets appended as a query parameter.
    """
    pass  # Not used — see tunnel() below


@sock.route('/<int:ap_id>/tunnel', bp=bp)
def tunnel(ws, ap_id):
    """WebSocket ↔ guacd TCP transparent relay.

    Guacamole.Client JS drives the full Guacamole protocol handshake.
    We just relay raw Guacamole instruction text between the browser
    WebSocket and the guacd TCP socket.

    The JS client calls connect('') which opens ws://.../tunnel?
    Then it sends 'select,<proto>;' and the full handshake proceeds
    over the relay.

    We intercept the first 'select' instruction from the browser and
    rewrite it to inject the correct protocol.  Then we intercept the
    'connect' instruction to inject our stored credentials and settings.
    """
    if not current_user.is_authenticated:
        ws.close(reason='Not authenticated')
        return

    ap = db.session.get(AccessPoint, ap_id)
    if not ap or not ap.is_enabled:
        ws.close(reason='Access point not found')
        return

    resource = db.session.get(Resource, ap.resource_id)
    if not resource or not can_user_access(current_user, resource):
        ws.close(reason='Access denied')
        return

    guacd_host = current_app.config.get('GUACD_HOST', 'localhost')
    guacd_port = current_app.config.get('GUACD_PORT', 4822)

    # Open raw TCP connection to guacd
    guacd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    guacd_sock.settimeout(20)
    try:
        guacd_sock.connect((guacd_host, guacd_port))
    except Exception as e:
        current_app.logger.error(f'Cannot connect to guacd at {guacd_host}:{guacd_port}: {e}')
        ws.close(reason='Cannot reach guacd')
        return

    # Build the connection parameters we'll inject
    connect_params = {
        'hostname': ap.hostname,
        'port': str(ap.effective_port),
        'username': ap.username or '',
        'password': ap.password or '',
    }
    if ap.protocol == 'rdp':
        connect_params.update({
            'security': 'any',
            'ignore-cert': 'true',
            'resize-method': 'display-update',
        })
    elif ap.protocol == 'ssh':
        connect_params.update({
            'color-scheme': 'gray-black',
            'font-size': '14',
            'terminal-type': 'xterm-256color',
        })

    # Update access tracking
    from datetime import datetime, timezone
    ap.last_accessed_by = current_user.id
    ap.last_accessed_at = datetime.now(timezone.utc)
    db.session.commit()

    closed = threading.Event()

    # Phase tracking for instruction interception
    phase = {'state': 'select', 'args_list': None}

    def _encode_instruction(opcode, args):
        """Encode a Guacamole protocol instruction."""
        parts = [opcode] + list(args)
        return ','.join(f'{len(str(p))}.{p}' for p in parts) + ';'

    def _parse_instruction(data):
        """Parse a single Guacamole instruction from text.
        Returns (opcode, [args], remaining_data) or None if incomplete.
        """
        idx = 0
        elements = []
        while idx < len(data):
            # Find the dot separating length from value
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
            # Next char should be ',' (more elements) or ';' (end)
            if value_end >= len(data):
                return None
            separator = data[value_end]
            idx = value_end + 1
            if separator == ';':
                if not elements:
                    return None
                return (elements[0], elements[1:], data[idx:])
            elif separator != ',':
                return None
        return None

    def guacd_to_browser():
        """Read from guacd TCP socket, forward to browser WebSocket."""
        buf = b''
        try:
            while not closed.is_set():
                try:
                    chunk = guacd_sock.recv(65536)
                except socket.timeout:
                    continue
                except Exception:
                    break
                if not chunk:
                    break

                # guacd sends UTF-8 Guacamole instructions
                text = chunk.decode('utf-8', errors='replace')

                # If we're in the handshake phase, intercept 'args' response
                if phase['state'] == 'wait_args':
                    parsed = _parse_instruction(text)
                    if parsed and parsed[0] == 'args':
                        phase['args_list'] = parsed[1]
                        phase['state'] = 'handshake'
                        # Forward the args instruction to the browser
                        ws.send(text)
                        continue

                ws.send(text)
        except Exception:
            pass
        finally:
            closed.set()

    reader_thread = threading.Thread(target=guacd_to_browser, daemon=True)
    reader_thread.start()

    try:
        # The JS client sends instructions; we intercept and rewrite
        # during the handshake phase, then relay transparently.
        while not closed.is_set():
            try:
                data = ws.receive(timeout=1)
            except Exception:
                break
            if data is None:
                break

            if phase['state'] == 'select':
                # Browser sends: select,<protocol>;
                # We rewrite to ensure the correct protocol from the AP
                instruction = _encode_instruction('select', [ap.protocol])
                guacd_sock.sendall(instruction.encode('utf-8'))
                phase['state'] = 'wait_args'
                continue

            if phase['state'] == 'handshake':
                # Browser sends size, audio, video, image instructions
                # then finally 'connect' — we intercept 'connect' to
                # inject our credentials
                parsed = _parse_instruction(data)
                if parsed and parsed[0] == 'connect':
                    # Build connect args matching the 'args' list from guacd
                    args = []
                    for arg_name in (phase['args_list'] or []):
                        args.append(connect_params.get(arg_name, ''))
                    instruction = _encode_instruction('connect', args)
                    guacd_sock.sendall(instruction.encode('utf-8'))
                    phase['state'] = 'connected'
                    continue
                else:
                    # Forward size, audio, video, image as-is
                    guacd_sock.sendall(data.encode('utf-8') if isinstance(data, str) else data)
                    continue

            # Normal relay after handshake
            raw = data.encode('utf-8') if isinstance(data, str) else data
            guacd_sock.sendall(raw)

    except Exception as e:
        current_app.logger.error(f'Guacamole tunnel error for AP {ap_id}: {e}')
    finally:
        closed.set()
        reader_thread.join(timeout=5)
        try:
            guacd_sock.close()
        except Exception:
            pass
