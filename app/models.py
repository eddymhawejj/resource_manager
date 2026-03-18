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
    ip_address = db.Column(db.String(45), nullable=True)
    resource_type = db.Column(db.String(50), nullable=False, default='testbed')
    location = db.Column(db.String(100), default='')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    parent_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=True)

    children = db.relationship('Resource', backref=db.backref('parent', remote_side='Resource.id'), lazy='dynamic',
                               cascade='all, delete-orphan')
    ping_results = db.relationship('PingResult', backref='resource', lazy='dynamic',
                                   order_by='PingResult.checked_at.desc()',
                                   cascade='all, delete-orphan')
    bookings = db.relationship('Booking', backref='resource', lazy='dynamic',
                               cascade='all, delete-orphan')

    @property
    def is_testbed(self):
        return self.parent_id is None

    @property
    def latest_ping(self):
        return self.ping_results.first()

    @property
    def is_reachable(self):
        ping = self.latest_ping
        return ping.is_reachable if ping else None

    def __repr__(self):
        return f'<Resource {self.name}>'


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
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

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
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    is_reachable = db.Column(db.Boolean, nullable=False)
    response_time_ms = db.Column(db.Float, nullable=True)
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
