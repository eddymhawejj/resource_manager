import selectors
import socket

from flask import (
    current_app, render_template, abort, url_for,
)
from flask_login import login_required, current_user
from flask_sock import Sock

from app.extensions import db
from app.models import AccessPoint, Resource, can_user_access
from app.console import bp

# Monkey-patch simple-websocket to disable PerMessageDeflate.
# The library hardcodes compression in AcceptConnection which corrupts
# Guacamole protocol frames in the browser.
import simple_websocket.ws as _sw
from wsproto.events import (
    Request as _WsRequest,
    AcceptConnection as _WsAcceptConnection,
    CloseConnection as _WsCloseConnection,
    Ping as _WsPing,
    Pong as _WsPong,
    TextMessage as _WsTextMessage,
    BytesMessage as _WsBytesMessage,
)
from wsproto.frame_protocol import CloseReason as _WsCloseReason
from wsproto.utilities import LocalProtocolError as _WsLocalProtocolError


def _handle_events_no_deflate(self):
    keep_going = True
    out_data = b''
    for event in self.ws.events():
        try:
            if isinstance(event, _WsRequest):
                self.subprotocol = self.choose_subprotocol(event)
                # NO extensions — PerMessageDeflate removed
                out_data += self.ws.send(_WsAcceptConnection(
                    subprotocol=self.subprotocol))
            elif isinstance(event, _WsCloseConnection):
                if self.is_server:
                    out_data += self.ws.send(event.response())
                self.close_reason = event.code
                self.close_message = event.reason
                self.connected = False
                self.event.set()
                keep_going = False
            elif isinstance(event, _WsPing):
                out_data += self.ws.send(event.response())
            elif isinstance(event, _WsPong):
                self.pong_received = True
            elif isinstance(event, (_WsTextMessage, _WsBytesMessage)):
                self.incoming_message_len += len(event.data)
                if self.max_message_size and \
                        self.incoming_message_len > self.max_message_size:
                    out_data += self.ws.send(_WsCloseConnection(
                        _WsCloseReason.MESSAGE_TOO_BIG, 'Message is too big'))
                    self.event.set()
                    keep_going = False
                    break
                if self.incoming_message is None:
                    self.incoming_message = event.data
                elif isinstance(event, _WsTextMessage):
                    if not isinstance(self.incoming_message, bytearray):
                        self.incoming_message = bytearray(
                            (self.incoming_message + event.data).encode())
                    else:
                        self.incoming_message += event.data.encode()
                else:
                    if not isinstance(self.incoming_message, bytearray):
                        self.incoming_message = bytearray(
                            self.incoming_message + event.data)
                    else:
                        self.incoming_message += event.data
                if not event.message_finished:
                    continue
                if isinstance(self.incoming_message, (str, bytes)):
                    self.input_buffer.append(self.incoming_message)
                elif isinstance(event, _WsTextMessage):
                    self.input_buffer.append(self.incoming_message.decode())
                else:
                    self.input_buffer.append(bytes(self.incoming_message))
                self.incoming_message = None
                self.incoming_message_len = 0
                self.event.set()
        except _WsLocalProtocolError:
            out_data = b''
            self.event.set()
            keep_going = False
    if out_data:
        self.sock.send(out_data)
    return keep_going


_sw.Base._handle_events = _handle_events_no_deflate

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


@sock.route('/<int:ap_id>/tunnel', bp=bp)
def tunnel(ws, ap_id):
    """WebSocket <-> guacd TCP relay.

    Uses pyguacamole's GuacamoleClient for a clean server-side handshake
    with guacd, then relays Guacamole instructions between the browser
    and guacd in a single-threaded select loop.

    The JS Guacamole.Client drives the protocol through the WebSocket.
    We intercept 'select' and 'connect' during the handshake to inject
    the correct protocol and credentials from the access point.
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
        current_app.logger.error(
            f'Cannot connect to guacd at {guacd_host}:{guacd_port}: {e}')
        ws.close(reason='Cannot reach guacd')
        return

    guacd_sock.setblocking(False)

    # Connection parameters to inject during handshake
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

    # Handshake phase tracking
    phase = 'select'  # select -> wait_args -> handshake -> connected
    args_list = None

    # --- Phase 1: Do the guacd handshake synchronously before entering
    # the relay loop.  This avoids threading issues entirely. ---

    try:
        # Step 1: Send 'select' with protocol
        guacd_sock.setblocking(True)
        guacd_sock.settimeout(10)
        instruction = _encode_instruction('select', [ap.protocol])
        guacd_sock.sendall(instruction.encode('utf-8'))

        # Step 2: Receive 'args' from guacd
        buf = b''
        while b';' not in buf:
            chunk = guacd_sock.recv(4096)
            if not chunk:
                raise ConnectionError('guacd closed during handshake')
            buf += chunk

        args_text = buf.decode('utf-8')
        parsed = _parse_instruction(args_text)
        if not parsed or parsed[0] != 'args':
            raise ConnectionError(f'Expected args from guacd, got: {args_text[:100]}')
        args_list = parsed[1]

        # Step 3: Send size, audio, video, image, connect
        guacd_sock.sendall(
            _encode_instruction('size', ['1920', '1080', '96']).encode('utf-8'))
        guacd_sock.sendall(
            _encode_instruction('audio', ['audio/L16']).encode('utf-8'))
        guacd_sock.sendall(
            _encode_instruction('video', []).encode('utf-8'))
        guacd_sock.sendall(
            _encode_instruction('image', []).encode('utf-8'))

        # Build connect args matching the args list from guacd
        connect_args = []
        for arg_name in args_list:
            connect_args.append(connect_params.get(arg_name, ''))
        guacd_sock.sendall(
            _encode_instruction('connect', connect_args).encode('utf-8'))

        # Step 4: Receive 'ready' from guacd
        buf = b''
        while b';' not in buf:
            chunk = guacd_sock.recv(4096)
            if not chunk:
                raise ConnectionError('guacd closed during handshake')
            buf += chunk

        ready_text = buf.decode('utf-8')
        parsed = _parse_instruction(ready_text)
        if not parsed or parsed[0] != 'ready':
            raise ConnectionError(
                f'Expected ready from guacd, got: {ready_text[:100]}')

        guacd_sock.setblocking(False)

    except Exception as e:
        current_app.logger.error(f'guacd handshake failed for AP {ap_id}: {e}')
        guacd_sock.close()
        ws.close(reason='guacd handshake failed')
        return

    # --- Phase 2: Relay loop. Single-threaded: poll guacd with select,
    # receive from browser with a short timeout. ---

    sel = selectors.DefaultSelector()
    sel.register(guacd_sock, selectors.EVENT_READ)

    try:
        while True:
            # Check if guacd has data to send to browser
            events = sel.select(timeout=0)
            for key, mask in events:
                try:
                    chunk = guacd_sock.recv(65536)
                except (BlockingIOError, socket.error):
                    chunk = None
                if not chunk:
                    return  # guacd disconnected
                ws.send(chunk.decode('utf-8', errors='replace'))

            # Check if browser has data to send to guacd
            try:
                data = ws.receive(timeout=0.02)
            except Exception:
                return  # browser disconnected
            if data is None:
                return

            raw = data.encode('utf-8') if isinstance(data, str) else data
            guacd_sock.sendall(raw)

    except Exception as e:
        current_app.logger.error(f'Guacamole tunnel error for AP {ap_id}: {e}')
    finally:
        sel.close()
        try:
            guacd_sock.close()
        except Exception:
            pass
