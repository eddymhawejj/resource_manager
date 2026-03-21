# Resource Manager

Flask-based lab resource management system with booking, ICMP monitoring, and calendar integration.

## Quick Start

```bash
pip install -r requirements.txt
docker compose up -d   # Starts PostgreSQL, Caddy, guacd, guacamole-lite, and Flask app
flask init-db          # Creates DB tables + default admin (admin/admin)
python run.py          # Starts dev server on :5000 (dev only)
```

## Production Deployment

```bash
# Set domain and secrets in .env
DOMAIN=lab.example.com
SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
POSTGRES_PASSWORD=$(python -c "import secrets; print(secrets.token_hex(16))")
GUACLITE_URL=wss://lab.example.com/websocket-tunnel

# Start all services (Caddy auto-provisions TLS via Let's Encrypt)
docker compose up -d
```

Architecture: `Browser → Caddy (HTTPS/WSS, :443) → Flask (:5000) / guacamole-lite (:8080) → guacd (:4822) + PostgreSQL (:5432)`

Gunicorn auto-scales to `2x CPU + 1` workers. Override with `GUNICORN_WORKERS`.

## Project Structure

```
app/
  __init__.py          # App factory, auto-migration, scheduler setup
  config.py            # Config from environment / .env
  extensions.py        # Flask extensions (db, migrate, login, mail, csrf)
  models.py            # All models: User, Resource, ResourceHost, Vlan, Subnet, Booking, PingResult, AppSettings
  email_service.py     # Email sending with .ics calendar invites (Outlook sync)
  auth/                # Login, registration, LDAP auth
  resources/           # Resource CRUD, host management (multi-IP support)
  bookings/            # Booking CRUD, calendar view, conflict detection
  console/             # In-browser RDP/SSH via guacamole-lite + guacd
    routes.py          # File transfer, diagnostics, Python relay fallback
    token.py           # AES-256-CBC token encryption for guacamole-lite
  monitoring/          # ICMP ping service, dashboard, status badges
  network/             # VLAN/subnet management, network overview, auto-linking
  admin/               # Admin panel, SMTP settings, branding
  templates/           # Jinja2 templates (base.html + per-module)
  static/              # CSS, JS, uploads
```

## Key Architectural Decisions

- **PostgreSQL** is the production database. `_auto_migrate()` uses `db.create_all()` + portable ALTER TABLE to ensure all tables and columns exist on startup — no Alembic commands needed for schema changes. Tests use SQLite in-memory for speed.
- **Multi-host resources**: Each `Resource` has multiple `ResourceHost` entries (IP/hostname). Hosts have a `critical` flag — only critical hosts affect resource status (online/offline/degraded).
- **PingResult** links to `ResourceHost` (via `host_id`), not directly to `Resource`.
- **Booking emails** include `.ics` calendar attachments so Outlook/Google Calendar auto-create events. Cancellation emails send `METHOD:CANCEL` to remove events.
- **APScheduler** runs ping monitoring in background (configurable interval).
- **SMTP** can be configured via environment variables or runtime via admin panel (`AppSettings` table).
- **VLAN/Subnet mapping**: `Vlan` → `Subnet` → `ResourceHost`. When a host IP is added, it auto-links to the matching subnet. Network overview page shows the full lab topology.
- **Console relay**: guacamole-lite (Node.js) is the primary WebSocket↔guacd relay for in-browser RDP/SSH. A Python relay fallback exists but is disabled by default (`GUAC_PYTHON_RELAY_ENABLED=false`) to prevent silent fallback masking connectivity issues. Connection tokens are AES-256-CBC encrypted.

## Models

- `Resource` — testbed (parent) or child resource. Status aggregated from hosts or children.
- `ResourceHost` — IP/hostname for a resource. Has `address`, `label`, `critical`, `subnet_id` fields. Auto-links to matching subnet.
- `Vlan` — VLAN definition with number (1-4094), name, description. Has many subnets.
- `Subnet` — Network subnet in CIDR notation, belongs to a VLAN. Has gateway, name, description. Hosts auto-link by IP match.
- `Booking` — time-slot reservation with `calendar_uid` for .ics event matching.
- `PingResult` — ICMP ping result linked to a host.
- `User` — local or LDAP auth, admin/user roles.
- `AccessPoint` — RDP/SSH connection endpoint for a resource. Has protocol, hostname, port, credentials, and `is_enabled` flag.
- `AppSettings` — key-value store for runtime config (SMTP, branding).

## Environment Variables

See `.env.example`. Key ones:
- `DATABASE_URL` — SQLAlchemy URI (default: `postgresql://resmanager:changeme@localhost:5432/resource_manager`)
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD` — SMTP config
- `LDAP_ENABLED`, `LDAP_URL` — LDAP authentication
- `PING_INTERVAL_SECONDS` — monitoring frequency (default: 60)
- `GUACLITE_URL` — guacamole-lite WebSocket URL (default: `ws://localhost:8080`)
- `GUACLITE_SECRET_KEY` — shared secret for token encryption
- `GUAC_PYTHON_RELAY_ENABLED` — enable Python WebSocket relay fallback (default: `false`)
- `DOMAIN` — domain for Caddy reverse proxy (default: `localhost`)
- `POSTGRES_PASSWORD` — PostgreSQL password (default: `changeme`)
- `PG_POOL_SIZE` — SQLAlchemy connection pool size (default: 5)
- `PG_MAX_OVERFLOW` — SQLAlchemy max overflow connections (default: 10)
- `GUNICORN_WORKERS` — override auto-scaled worker count (default: `2x CPU + 1`)

## Testing

```bash
pip install pytest
pytest                # Run all 161 tests
pytest -x             # Stop on first failure
pytest tests/test_models.py  # Run a specific file
```

Tests cover models, auth, resources, bookings, monitoring, network, and admin routes. CI runs on every push via GitHub Actions.

## Development Notes

- Auto-migration handles schema evolution. When adding new columns to models, add them to `_column_defs` in `_auto_migrate()` in `app/__init__.py`.
- Forms use WTForms with CSRF protection. Host management uses raw form arrays (`host_addresses[]`, `host_labels[]`, `host_critical[]`) parsed in `_sync_hosts_from_form()`.
- Templates use Bootstrap 5.3 + HTMX for live status updates + FullCalendar for booking calendar.
