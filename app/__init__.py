import os

from flask import Flask, render_template

from app.config import Config
from app.extensions import db, migrate, login_manager, mail, csrf


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(os.path.join(app.static_folder, 'uploads'), exist_ok=True)

    # Init extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)

    # User loader
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Context processor for templates
    from app.models import AppSettings

    @app.context_processor
    def inject_settings():
        logo_path = AppSettings.get('logo_path', '')
        app_name = AppSettings.get('app_name', 'Resource Manager')
        return dict(logo_path=logo_path, app_name=app_name)

    # Register blueprints
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp)

    from app.resources import bp as resources_bp
    app.register_blueprint(resources_bp)

    from app.bookings import bp as bookings_bp
    app.register_blueprint(bookings_bp)

    from app.monitoring import bp as monitoring_bp
    app.register_blueprint(monitoring_bp)

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp)

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    # Index redirect
    @app.route('/')
    def index():
        from flask import redirect, url_for
        return redirect(url_for('resources.list_resources'))

    # Auto-migrate schema for new columns
    with app.app_context():
        _auto_migrate(db)

    # CLI commands
    register_cli(app)

    # Start monitoring scheduler
    start_scheduler(app)

    return app


def _auto_migrate(db):
    """Add missing columns/tables to existing database without requiring a full migration."""
    import sqlalchemy
    inspector = sqlalchemy.inspect(db.engine)
    tables = inspector.get_table_names()

    # Skip if this is a fresh database (no tables yet)
    if 'resources' not in tables:
        return

    # Add resolved_ip to ping_results if missing
    if 'ping_results' in tables:
        columns = [c['name'] for c in inspector.get_columns('ping_results')]
        if 'resolved_ip' not in columns:
            db.session.execute(sqlalchemy.text(
                'ALTER TABLE ping_results ADD COLUMN resolved_ip VARCHAR(45)'
            ))
            db.session.commit()

    # Create resource_hosts table if missing
    if 'resource_hosts' not in tables:
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE resource_hosts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL REFERENCES resources(id),
                address VARCHAR(255) NOT NULL,
                label VARCHAR(100) NOT NULL DEFAULT ''
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_resource_hosts_resource_id ON resource_hosts (resource_id)'
        ))
        db.session.commit()

        # Migrate existing ip_address values into resource_hosts
        rows = db.session.execute(sqlalchemy.text(
            "SELECT id, ip_address FROM resources WHERE ip_address IS NOT NULL AND ip_address != ''"
        )).fetchall()
        for row in rows:
            db.session.execute(sqlalchemy.text(
                'INSERT INTO resource_hosts (resource_id, address, label) VALUES (:rid, :addr, :label)'
            ), {'rid': row[0], 'addr': row[1], 'label': ''})
        if rows:
            db.session.commit()

    # Migrate ping_results: add host_id, relax resource_id NOT NULL
    if 'ping_results' in tables:
        columns = [c['name'] for c in inspector.get_columns('ping_results')]
        if 'host_id' not in columns:
            # SQLite can't ALTER COLUMN, so recreate the table
            db.session.execute(sqlalchemy.text('''
                CREATE TABLE ping_results_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER REFERENCES resource_hosts(id),
                    resource_id INTEGER,
                    is_reachable BOOLEAN NOT NULL,
                    response_time_ms FLOAT,
                    resolved_ip VARCHAR(45),
                    checked_at DATETIME
                )
            '''))
            db.session.execute(sqlalchemy.text('''
                INSERT INTO ping_results_new (id, resource_id, is_reachable, response_time_ms, resolved_ip, checked_at)
                SELECT id, resource_id, is_reachable, response_time_ms, resolved_ip, checked_at
                FROM ping_results
            '''))
            db.session.execute(sqlalchemy.text('DROP TABLE ping_results'))
            db.session.execute(sqlalchemy.text('ALTER TABLE ping_results_new RENAME TO ping_results'))
            db.session.execute(sqlalchemy.text(
                'CREATE INDEX ix_ping_results_host_id ON ping_results (host_id)'
            ))
            db.session.execute(sqlalchemy.text(
                'CREATE INDEX ix_ping_results_checked_at ON ping_results (checked_at)'
            ))
            db.session.commit()

            # Migrate existing ping_results to link to their host
            db.session.execute(sqlalchemy.text('''
                UPDATE ping_results
                SET host_id = (
                    SELECT rh.id FROM resource_hosts rh
                    WHERE rh.resource_id = ping_results.resource_id
                    LIMIT 1
                )
                WHERE host_id IS NULL AND resource_id IS NOT NULL
            '''))
            db.session.commit()


def register_cli(app):
    import click

    @app.cli.command('init-db')
    def init_db():
        """Initialize the database with default data."""
        from app.models import User, AppSettings
        db.create_all()

        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                email='admin@localhost',
                display_name='Administrator',
                role='admin',
                auth_type='local',
            )
            admin.set_password('admin')
            db.session.add(admin)
            db.session.commit()
            click.echo('Created default admin user (admin/admin)')
        else:
            click.echo('Admin user already exists')

        if not AppSettings.query.get('app_name'):
            AppSettings.set('app_name', 'Resource Manager')
            click.echo('Initialized default settings')

        click.echo('Database initialized successfully.')


def start_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    from app.monitoring.ping_service import ping_all_resources

    scheduler = BackgroundScheduler(daemon=True)
    interval = app.config.get('PING_INTERVAL_SECONDS', 60)

    scheduler.add_job(
        func=ping_all_resources,
        trigger='interval',
        seconds=interval,
        args=[app],
        id='ping_monitor',
        replace_existing=True,
    )
    scheduler.start()
    app.scheduler = scheduler
