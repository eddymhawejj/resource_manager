import uuid
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)
    display_name = db.Column(db.String(120), nullable=False, default='')
    role = db.Column(db.String(20), nullable=False, default='user')
    auth_type = db.Column(db.String(20), nullable=False, default='local')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    bookings = db.relationship('Booking', backref='user', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def __repr__(self):
        return f'<User {self.username}>'


class Resource(db.Model):
    __tablename__ = 'resources'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default='')
    ip_address = db.Column(db.String(255), nullable=True)  # Legacy, kept for migration
    resource_type = db.Column(db.String(50), nullable=False, default='testbed')
    location = db.Column(db.String(100), default='')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    parent_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=True)

    children = db.relationship('Resource', backref=db.backref('parent', remote_side='Resource.id'), lazy='dynamic',
                               cascade='all, delete-orphan')
    hosts = db.relationship('ResourceHost', backref='resource', lazy='dynamic',
                            order_by='ResourceHost.label',
                            cascade='all, delete-orphan')
    bookings = db.relationship('Booking', backref='resource', lazy='dynamic',
                               cascade='all, delete-orphan')

    @property
    def is_testbed(self):
        return self.parent_id is None

    @property
    def host_statuses(self):
        """Return list of (host, status) for all hosts."""
        results = []
        for host in self.hosts.all():
            ping = host.latest_ping
            if ping is None:
                results.append((host, 'unknown'))
            elif ping.is_reachable:
                results.append((host, 'online'))
            else:
                results.append((host, 'offline'))
        return results

    @property
    def status(self):
        """Return 'online', 'offline', 'degraded', or 'unknown'.

        Only hosts marked as critical affect the overall status.
        Non-critical hosts are informational and don't trigger degraded.
        """
        host_list = self.hosts.all()
        if host_list:
            # Only critical hosts determine the resource status
            critical_statuses = []
            for host in host_list:
                if not host.critical:
                    continue
                ping = host.latest_ping
                if ping is None:
                    critical_statuses.append('unknown')
                elif ping.is_reachable:
                    critical_statuses.append('online')
                else:
                    critical_statuses.append('offline')

            # If no critical hosts, treat as unknown (informational-only hosts)
            if not critical_statuses:
                return 'unknown'

            if all(s == 'online' for s in critical_statuses):
                return 'online'
            if all(s == 'offline' for s in critical_statuses):
                return 'offline'
            if any(s == 'offline' or s == 'degraded' for s in critical_statuses):
                return 'degraded'
            return 'unknown'

        # No hosts: aggregate active children statuses
        child_list = [c for c in self.children.all() if c.is_active]
        if not child_list:
            return 'unknown'

        statuses = [c.status for c in child_list]
        if all(s == 'online' for s in statuses):
            return 'online'
        if all(s == 'offline' for s in statuses):
            return 'offline'
        if any(s == 'offline' or s == 'degraded' for s in statuses):
            return 'degraded'
        return 'unknown'

    @property
    def is_reachable(self):
        s = self.status
        if s == 'online':
            return True
        if s == 'offline':
            return False
        return None

    def __repr__(self):
        return f'<Resource {self.name}>'


class ResourceHost(db.Model):
    __tablename__ = 'resource_hosts'

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    address = db.Column(db.String(255), nullable=False)
    label = db.Column(db.String(100), nullable=False, default='')
    critical = db.Column(db.Boolean, nullable=False, default=True)

    ping_results = db.relationship('PingResult', backref='host', lazy='dynamic',
                                   order_by='PingResult.checked_at.desc()',
                                   cascade='all, delete-orphan')

    @property
    def latest_ping(self):
        return self.ping_results.first()

    def __repr__(self):
        return f'<ResourceHost {self.label or self.address}>'


class Booking(db.Model):
    __tablename__ = 'bookings'

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='confirmed')
    calendar_uid = db.Column(db.String(64), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def ensure_calendar_uid(self):
        """Generate a stable calendar UID if not already set."""
        if not self.calendar_uid:
            self.calendar_uid = str(uuid.uuid4())
        return self.calendar_uid

    def has_conflict(self):
        """Check if this booking conflicts with existing confirmed bookings."""
        conflicts = Booking.query.filter(
            Booking.resource_id == self.resource_id,
            Booking.status == 'confirmed',
            Booking.id != self.id,
            Booking.start_time < self.end_time,
            Booking.end_time > self.start_time,
        ).first()
        return conflicts is not None

    def __repr__(self):
        return f'<Booking {self.title} ({self.start_time} - {self.end_time})>'


class PingResult(db.Model):
    __tablename__ = 'ping_results'

    id = db.Column(db.Integer, primary_key=True)
    host_id = db.Column(db.Integer, db.ForeignKey('resource_hosts.id'), nullable=True, index=True)
    resource_id = db.Column(db.Integer, nullable=True, index=True)  # Legacy, kept for migration
    is_reachable = db.Column(db.Boolean, nullable=False)
    response_time_ms = db.Column(db.Float, nullable=True)
    resolved_ip = db.Column(db.String(45), nullable=True)
    checked_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self):
        status = 'UP' if self.is_reachable else 'DOWN'
        return f'<PingResult {status} @ {self.checked_at}>'


class AppSettings(db.Model):
    __tablename__ = 'app_settings'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, default='')

    @staticmethod
    def get(key, default=''):
        setting = AppSettings.query.get(key)
        return setting.value if setting else default

    @staticmethod
    def set(key, value):
        setting = AppSettings.query.get(key)
        if setting:
            setting.value = value
        else:
            setting = AppSettings(key=key, value=value)
            db.session.add(setting)
        db.session.commit()

    def __repr__(self):
        return f'<AppSettings {self.key}={self.value}>'
