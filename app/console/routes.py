import threading

from flask import (
    current_app, render_template, abort, request, url_for,
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
    # Convert http(s) scheme to ws(s) for the WebSocket URL
    ws_tunnel_url = tunnel_url  # JS will build the full ws:// URL from location

    return render_template(
        'console/session.html',
        ap=ap,
        resource=resource,
        tunnel_path=ws_tunnel_url,
    )


@sock.route('/<int:ap_id>/tunnel', bp=bp)
def tunnel(ws, ap_id):
    """WebSocket relay between the browser and guacd."""
    from guacamole.client import GuacamoleClient

    # Auth check — flask-login session is available via cookie
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

    client = GuacamoleClient(guacd_host, guacd_port, timeout=20)

    try:
        # Build connection kwargs for guacd handshake
        connect_kwargs = {
            'hostname': ap.hostname,
            'port': str(ap.effective_port),
            'username': ap.username or '',
            'password': ap.password or '',
        }

        if ap.protocol == 'rdp':
            connect_kwargs.update({
                'security': 'any',
                'ignore_cert': 'true',
                'resize_method': 'display-update',
            })
        elif ap.protocol == 'ssh':
            connect_kwargs.update({
                'color_scheme': 'gray-black',
                'font_size': '14',
                'terminal_type': 'xterm-256color',
            })

        client.handshake(
            protocol=ap.protocol,
            width=1920,
            height=1080,
            dpi=96,
            audio=['audio/L16'],
            **connect_kwargs,
        )

        # Update access tracking
        ap.last_accessed_by = current_user.id
        from datetime import datetime, timezone
        ap.last_accessed_at = datetime.now(timezone.utc)
        db.session.commit()

        # Start a thread to relay guacd → browser
        closed = threading.Event()

        def guacd_to_browser():
            try:
                while not closed.is_set():
                    instruction = client.receive()
                    if not instruction:
                        break
                    ws.send(instruction)
            except Exception:
                pass
            finally:
                closed.set()

        reader_thread = threading.Thread(
            target=guacd_to_browser, daemon=True
        )
        reader_thread.start()

        # Main thread: browser → guacd
        while not closed.is_set():
            try:
                data = ws.receive(timeout=1)
            except Exception:
                break
            if data is None:
                break
            client.send(data)

        closed.set()
        reader_thread.join(timeout=5)

    except Exception as e:
        current_app.logger.error(f'Guacamole tunnel error for AP {ap_id}: {e}')
    finally:
        try:
            client.close()
        except Exception:
            pass
