# Resource Manager

Flask-based lab resource management system with booking, ICMP monitoring, and calendar integration.

## Quick Start

```bash
pip install -r requirements.txt
flask init-db          # Creates SQLite DB + default admin (admin/admin)
python run.py          # Starts dev server on :5000
```

## Project Structure

```
app/
  __init__.py          # App factory, auto-migration, scheduler setup
  config.py            # Config from environment / .env
  extensions.py        # Flask extensions (db, migrate, login, mail, csrf)
  models.py            # All models: User, Resource, ResourceHost, Booking, PingResult, AppSettings
  email_service.py     # Email sending with .ics calendar invites (Outlook sync)
  auth/                # Login, registration, LDAP auth
  resources/           # Resource CRUD, host management (multi-IP support)
  bookings/            # Booking CRUD, calendar view, conflict detection
  monitoring/          # ICMP ping service, dashboard, status badges
  admin/               # Admin panel, SMTP settings, branding
  templates/           # Jinja2 templates (base.html + per-module)
  static/              # CSS, JS, uploads
```

## Key Architectural Decisions

- **SQLite** with auto-migration in `_auto_migrate()` — no Alembic commands needed for schema changes. New columns/tables are added on startup.
- **Multi-host resources**: Each `Resource` has multiple `ResourceHost` entries (IP/hostname). Hosts have a `critical` flag — only critical hosts affect resource status (online/offline/degraded).
- **PingResult** links to `ResourceHost` (via `host_id`), not directly to `Resource`.
- **Booking emails** include `.ics` calendar attachments so Outlook/Google Calendar auto-create events. Cancellation emails send `METHOD:CANCEL` to remove events.
- **APScheduler** runs ping monitoring in background (configurable interval).
- **SMTP** can be configured via environment variables or runtime via admin panel (`AppSettings` table).

## Models

- `Resource` — testbed (parent) or child resource. Status aggregated from hosts or children.
- `ResourceHost` — IP/hostname for a resource. Has `address`, `label`, `critical` fields.
- `Booking` — time-slot reservation with `calendar_uid` for .ics event matching.
- `PingResult` — ICMP ping result linked to a host.
- `User` — local or LDAP auth, admin/user roles.
- `AppSettings` — key-value store for runtime config (SMTP, branding).

## Environment Variables

See `.env.example`. Key ones:
- `DATABASE_URL` — SQLAlchemy URI (default: `sqlite:///resource_manager.db`)
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD` — SMTP config
- `LDAP_ENABLED`, `LDAP_URL` — LDAP authentication
- `PING_INTERVAL_SECONDS` — monitoring frequency (default: 60)

## Testing

```bash
python -c "from app import create_app; app = create_app(); ..."
```

No formal test suite yet. Test via app factory + manual verification.

## Development Notes

- Auto-migration handles schema evolution for SQLite. When adding new columns, add migration logic to `_auto_migrate()` in `app/__init__.py`.
- Forms use WTForms with CSRF protection. Host management uses raw form arrays (`host_addresses[]`, `host_labels[]`, `host_critical[]`) parsed in `_sync_hosts_from_form()`.
- Templates use Bootstrap 5.3 + HTMX for live status updates + FullCalendar for booking calendar.
