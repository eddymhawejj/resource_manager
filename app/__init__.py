import os

from flask import Flask, render_template, url_for, request, jsonify, redirect

from app.config import Config
from app.extensions import db, migrate, login_manager, mail, csrf


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(os.path.join(app.static_folder, 'uploads'), exist_ok=True)

    # Ensure drive base directory exists and is world-writable
    drive_path = app.config.get('DRIVE_PATH', os.path.join(
        app.root_path, '..', 'data', 'drive'))
    os.makedirs(drive_path, mode=0o777, exist_ok=True)
    try:
        os.chmod(drive_path, 0o777)
    except OSError:
        pass

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

    from app.network import bp as network_bp
    app.register_blueprint(network_bp)

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp)

    from app.console import bp as console_bp
    app.register_blueprint(console_bp)

    # Init flask-sock for WebSocket support (Guacamole tunnel)
    from app.console.routes import init_sock
    init_sock(app)

    # JSON-aware error handling for AJAX/fetch requests
    def _wants_json():
        return (
            request.accept_mimetypes.best_match(['application/json', 'text/html']) == 'application/json'
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        )

    @login_manager.unauthorized_handler
    def unauthorized():
        if _wants_json():
            return jsonify({'error': 'Login required'}), 401
        return redirect(url_for('auth.login', next=request.url))

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify({'error': 'Not found'}), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        if _wants_json():
            return jsonify({'error': 'Forbidden'}), 403
        return render_template('errors/403.html'), 403

    @app.errorhandler(500)
    def server_error(e):
        if _wants_json():
            return jsonify({'error': str(e)}), 500
        return render_template('errors/500.html'), 500

    # Index redirect
    @app.route('/')
    def index():
        from flask import redirect, url_for
        return redirect(url_for('resources.list_resources'))

    # Global search API
    @app.route('/search')
    def global_search():
        from flask import request, jsonify
        from flask_login import current_user
        from app.models import Resource, ResourceHost, Vlan, Subnet, Booking
        if not current_user.is_authenticated:
            return jsonify([]), 401
        q = request.args.get('q', '').strip()
        if len(q) < 2:
            return jsonify([])
        results = []
        like = f'%{q}%'
        # Resources
        for r in Resource.query.filter(
            (Resource.name.ilike(like)) | (Resource.description.ilike(like)) | (Resource.location.ilike(like))
        ).limit(8).all():
            results.append({'type': 'resource', 'icon': 'bi-hdd-stack', 'label': r.name,
                            'detail': r.resource_type.title() + (' — ' + r.location if r.location else ''),
                            'url': url_for('resources.detail', resource_id=r.id)})
        # Hosts (IP / hostname)
        for h in ResourceHost.query.filter(
            (ResourceHost.address.ilike(like)) | (ResourceHost.label.ilike(like))
        ).limit(8).all():
            results.append({'type': 'host', 'icon': 'bi-hdd-network', 'label': h.address,
                            'detail': (h.label + ' — ' if h.label else '') + (h.resource.name if h.resource else ''),
                            'url': url_for('resources.detail', resource_id=h.resource_id)})
        # VLANs
        for v in Vlan.query.filter(
            (Vlan.name.ilike(like)) | (Vlan.description.ilike(like)) | (db.cast(Vlan.number, db.String).ilike(like))
        ).limit(5).all():
            results.append({'type': 'vlan', 'icon': 'bi-diagram-2', 'label': f'VLAN {v.number} — {v.name}',
                            'detail': v.description[:80] if v.description else '',
                            'url': url_for('network.vlan_detail', vlan_id=v.id)})
        # Subnets
        for s in Subnet.query.filter(
            (Subnet.cidr.ilike(like)) | (Subnet.name.ilike(like))
        ).limit(5).all():
            results.append({'type': 'subnet', 'icon': 'bi-grid-3x3', 'label': s.cidr,
                            'detail': s.name or '',
                            'url': url_for('network.vlan_detail', vlan_id=s.vlan_id)})
        # Bookings
        for b in Booking.query.filter(Booking.title.ilike(like)).limit(5).all():
            results.append({'type': 'booking', 'icon': 'bi-bookmark-check', 'label': b.title,
                            'detail': b.resource.name if b.resource else '',
                            'url': url_for('bookings.list_bookings')})
        return jsonify(results[:20])

    # Enable WAL mode for SQLite test databases
    if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
        from sqlalchemy import event

        with app.app_context():
            @event.listens_for(db.engine, 'connect')
            def _set_sqlite_pragmas(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute('PRAGMA journal_mode=WAL')
                cursor.execute('PRAGMA busy_timeout=30000')
                cursor.close()

    # Auto-migrate schema for new columns
    with app.app_context():
        _auto_migrate(db)

    # CLI commands
    register_cli(app)

    # Start monitoring scheduler
    start_scheduler(app)

    return app


def _auto_migrate(db):
    """Ensure all tables and columns exist.

    Uses SQLAlchemy's create_all() to create any missing tables from ORM models,
    then checks for columns that may have been added to models after the table
    was originally created.
    """
    import sqlalchemy

    db.create_all()

    inspector = sqlalchemy.inspect(db.engine)
    tables = inspector.get_table_names()

    # Add any columns that might be missing from an older schema
    _column_defs = {
        'ping_results': {
            'resolved_ip': 'VARCHAR(45)',
            'host_id': 'INTEGER REFERENCES resource_hosts(id)',
        },
        'resource_hosts': {
            'critical': 'BOOLEAN NOT NULL DEFAULT true',
            'subnet_id': 'INTEGER REFERENCES subnets(id)',
        },
        'bookings': {
            'calendar_uid': 'VARCHAR(64)',
        },
    }

    for table, columns in _column_defs.items():
        if table not in tables:
            continue
        existing = {c['name'] for c in inspector.get_columns(table)}
        for col_name, col_type in columns.items():
            if col_name not in existing:
                db.session.execute(sqlalchemy.text(
                    f'ALTER TABLE {table} ADD COLUMN {col_name} {col_type}'
                ))
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
    from app.network.switch_sync import sync_vlans_from_switch

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

    # VLAN sync from switch every 24 hours
    scheduler.add_job(
        func=sync_vlans_from_switch,
        trigger='interval',
        hours=24,
        args=[app],
        id='switch_vlan_sync',
        replace_existing=True,
    )

    # Automated subnet discovery scan (every 12 hours if enabled)
    auto_scan_interval = app.config.get('AUTO_SCAN_INTERVAL_HOURS', 0)
    if auto_scan_interval and auto_scan_interval > 0:
        from app.network.subnet_scan import start_scan_background

        def _auto_scan():
            start_scan_background(app)

        scheduler.add_job(
            func=_auto_scan,
            trigger='interval',
            hours=auto_scan_interval,
            id='auto_subnet_scan',
            replace_existing=True,
        )

    # Purge drive files older than 7 days (runs daily)
    from app.console.routes import purge_old_drive_files
    scheduler.add_job(
        func=purge_old_drive_files,
        trigger='interval',
        hours=24,
        args=[app],
        id='drive_file_purge',
        replace_existing=True,
    )

    scheduler.start()
    app.scheduler = scheduler
