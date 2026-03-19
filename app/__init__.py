import os

from flask import Flask, render_template, url_for

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

    from app.network import bp as network_bp
    app.register_blueprint(network_bp)

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

    # Enable WAL mode for SQLite on every connection (allows concurrent reads + writes)
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
                label VARCHAR(100) NOT NULL DEFAULT '',
                critical BOOLEAN NOT NULL DEFAULT 1
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

    # Add critical column to resource_hosts if missing
    if 'resource_hosts' in inspector.get_table_names():
        rh_columns = [c['name'] for c in inspector.get_columns('resource_hosts')]
        if 'critical' not in rh_columns:
            db.session.execute(sqlalchemy.text(
                'ALTER TABLE resource_hosts ADD COLUMN critical BOOLEAN NOT NULL DEFAULT 1'
            ))
            db.session.commit()

    # Create vlans table if missing
    if 'vlans' not in tables:
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE vlans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number INTEGER NOT NULL UNIQUE,
                name VARCHAR(100) NOT NULL DEFAULT '',
                description TEXT DEFAULT ''
            )
        '''))
        db.session.commit()

    # Create subnets table if missing
    if 'subnets' not in tables:
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE subnets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vlan_id INTEGER NOT NULL REFERENCES vlans(id),
                cidr VARCHAR(50) NOT NULL UNIQUE,
                name VARCHAR(100) NOT NULL DEFAULT '',
                gateway VARCHAR(45),
                description TEXT DEFAULT ''
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_subnets_vlan_id ON subnets (vlan_id)'
        ))
        db.session.commit()

    # Add subnet_id to resource_hosts if missing
    if 'resource_hosts' in inspector.get_table_names():
        rh_columns2 = [c['name'] for c in inspector.get_columns('resource_hosts')]
        if 'subnet_id' not in rh_columns2:
            db.session.execute(sqlalchemy.text(
                'ALTER TABLE resource_hosts ADD COLUMN subnet_id INTEGER REFERENCES subnets(id)'
            ))
            db.session.commit()

    # Add calendar_uid to bookings if missing
    if 'bookings' in tables:
        booking_columns = [c['name'] for c in inspector.get_columns('bookings')]
        if 'calendar_uid' not in booking_columns:
            db.session.execute(sqlalchemy.text(
                'ALTER TABLE bookings ADD COLUMN calendar_uid VARCHAR(64)'
            ))
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

    # Create tags table if missing
    if 'tags' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(50) NOT NULL UNIQUE,
                color VARCHAR(7) NOT NULL DEFAULT '#6c757d'
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE UNIQUE INDEX ix_tags_name ON tags (name)'
        ))
        db.session.commit()

    # Create resource_tags association table if missing
    if 'resource_tags' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE resource_tags (
                resource_id INTEGER NOT NULL REFERENCES resources(id),
                tag_id INTEGER NOT NULL REFERENCES tags(id),
                PRIMARY KEY (resource_id, tag_id)
            )
        '''))
        db.session.commit()

    # Create favorites table if missing
    if 'favorites' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                resource_id INTEGER NOT NULL REFERENCES resources(id),
                created_at DATETIME,
                UNIQUE (user_id, resource_id)
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_favorites_user_id ON favorites (user_id)'
        ))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_favorites_resource_id ON favorites (resource_id)'
        ))
        db.session.commit()

    # Create audit_log table if missing
    if 'audit_log' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                action VARCHAR(50) NOT NULL,
                target_type VARCHAR(50),
                target_id INTEGER,
                details TEXT,
                timestamp DATETIME
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_audit_log_user_id ON audit_log (user_id)'
        ))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_audit_log_timestamp ON audit_log (timestamp)'
        ))
        db.session.commit()

    # Create maintenance_windows table if missing
    if 'maintenance_windows' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE maintenance_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL REFERENCES resources(id),
                title VARCHAR(200) NOT NULL,
                start_time DATETIME NOT NULL,
                end_time DATETIME NOT NULL,
                notes TEXT,
                created_by INTEGER REFERENCES users(id),
                created_at DATETIME
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_maintenance_windows_resource_id ON maintenance_windows (resource_id)'
        ))
        db.session.commit()

    # Create alert_rules table if missing
    if 'alert_rules' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL REFERENCES resources(id),
                alert_type VARCHAR(20) NOT NULL DEFAULT 'email',
                target VARCHAR(500) NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                created_by INTEGER REFERENCES users(id),
                created_at DATETIME,
                last_triggered DATETIME
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_alert_rules_resource_id ON alert_rules (resource_id)'
        ))
        db.session.commit()

    # Create waitlist_entries table if missing
    if 'waitlist_entries' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE waitlist_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_id INTEGER NOT NULL REFERENCES resources(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                desired_start DATETIME NOT NULL,
                desired_end DATETIME NOT NULL,
                notes TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'waiting',
                created_at DATETIME,
                notified_at DATETIME
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_waitlist_entries_resource_id ON waitlist_entries (resource_id)'
        ))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_waitlist_entries_user_id ON waitlist_entries (user_id)'
        ))
        db.session.commit()

    # Create resource_assignments table if missing (shared children with slots)
    if 'resource_assignments' not in inspector.get_table_names():
        db.session.execute(sqlalchemy.text('''
            CREATE TABLE resource_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL REFERENCES resources(id),
                child_id INTEGER NOT NULL REFERENCES resources(id),
                slots INTEGER NOT NULL DEFAULT 1,
                notes TEXT DEFAULT '',
                created_at DATETIME,
                UNIQUE (parent_id, child_id)
            )
        '''))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_resource_assignments_parent_id ON resource_assignments (parent_id)'
        ))
        db.session.execute(sqlalchemy.text(
            'CREATE INDEX ix_resource_assignments_child_id ON resource_assignments (child_id)'
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

    scheduler.start()
    app.scheduler = scheduler
