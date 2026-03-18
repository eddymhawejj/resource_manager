# Resource Manager

A web application for managing local testbed resources, monitoring their health via ICMP ping, and scheduling bookings with a calendar interface.

## Technology Stack

### Backend

| Package | Version | Purpose |
|---------|---------|---------|
| Flask | 3.1.1 | Web framework |
| Flask-SQLAlchemy | 3.1.1 | ORM and database abstraction |
| Flask-Migrate | 4.1.0 | Database migrations (Alembic) |
| Flask-Login | 0.6.3 | User session management |
| Flask-WTF | 1.2.2 | Form handling and CSRF protection |
| Flask-Mail | 0.10.0 | SMTP email delivery |
| APScheduler | 3.10.4 | Background job scheduling |
| Werkzeug | 3.1.3 | WSGI utilities and password hashing |
| python-dotenv | 1.0.1 | Environment variable loading |
| email-validator | 2.1.1 | Email address validation |

### Frontend

| Library | Version | Source | Purpose |
|---------|---------|--------|---------|
| Bootstrap | 5.3.3 | CDN | Responsive CSS framework |
| Bootstrap Icons | 1.11.3 | CDN | Icon library |
| HTMX | 1.9.12 | CDN | Dynamic HTML updates without custom JS |
| FullCalendar | 6.1.11 | CDN | Interactive booking calendar |
| Inter | — | Google Fonts | UI typography |

Templates are rendered server-side with Jinja2. Custom styling and a dark/light theme toggle are handled via CSS custom properties and vanilla JavaScript.

### Database

- **SQLite** — default, zero-configuration database stored at `instance/resource_manager.db`
- Managed through SQLAlchemy ORM with Flask-Migrate for schema migrations
- Configurable via `DATABASE_URL` to use any SQLAlchemy-supported backend

## Features

- **Resource Management** — organize testbeds and child resources in a hierarchical structure
- **ICMP Monitoring** — background ping checks with status badges and response time history
- **Booking System** — calendar-based scheduling with overlap detection
- **Authentication** — local accounts with password hashing, optional LDAP integration
- **Email Notifications** — booking confirmation and cancellation emails via SMTP
- **Admin Panel** — user management, runtime SMTP/LDAP configuration, logo upload
- **Theming** — dark and light mode with persistent toggle

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize the database and seed the admin user
flask db upgrade
flask init-db

# Run the application
python run.py
```

The app starts at **http://localhost:5000**. Log in with the seeded admin credentials.

## Configuration

Copy `.env.example` to `.env` and adjust values as needed:

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `change-me-...` | Flask session secret |
| `DATABASE_URL` | `sqlite:///resource_manager.db` | Database connection string |
| `MAIL_SERVER` | — | SMTP server hostname |
| `MAIL_PORT` | `587` | SMTP port |
| `MAIL_USE_TLS` | `true` | Enable TLS |
| `MAIL_USERNAME` / `MAIL_PASSWORD` | — | SMTP credentials |
| `LDAP_ENABLED` | `false` | Enable LDAP authentication |
| `LDAP_URL` | `ldap://ldap.example.com` | LDAP server URL |
| `LDAP_BASE_DN` | `dc=example,dc=com` | LDAP search base |
| `PING_INTERVAL_SECONDS` | `60` | Monitoring ping interval |
| `PING_TIMEOUT_SECONDS` | `2` | Ping timeout |
| `PING_HISTORY_LIMIT` | `100` | Max ping records per resource |

SMTP and LDAP settings can also be configured at runtime from the admin settings page.

## Project Structure

```
resource_manager/
├── run.py                  # Entry point
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py         # Application factory (create_app)
│   ├── config.py           # Configuration from environment
│   ├── extensions.py       # Flask extension instances
│   ├── models.py           # SQLAlchemy models
│   ├── email_service.py    # SMTP email helper
│   ├── auth/               # Authentication blueprint (local + LDAP)
│   ├── resources/          # Resource CRUD blueprint
│   ├── bookings/           # Booking and calendar blueprint
│   ├── monitoring/         # Ping service and status blueprint
│   ├── admin/              # Admin panel blueprint
│   ├── templates/          # Jinja2 HTML templates
│   └── static/             # CSS, JS, uploads
├── migrations/             # Alembic migration scripts
└── instance/               # SQLite database file
```
