import base64
import ipaddress
import json
import uuid
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


# ===== Association table for Resource <-> Tag many-to-many =====
resource_tags = db.Table(
    'resource_tags',
    db.Column('resource_id', db.Integer, db.ForeignKey('resources.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tags.id'), primary_key=True),
)


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
    tags = db.relationship('Tag', secondary=resource_tags, back_populates='resources', lazy='dynamic')

    @property
    def is_testbed(self):
        return self.parent_id is None

    @property
    def host_statuses(self):
        """Return list of (host, status) for all hosts.

        A host is only 'offline' if the last 3 consecutive pings all failed.
        """
        results = []
        for host in self.hosts.all():
            results.append((host, host.ping_status))
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
                critical_statuses.append(host.ping_status)

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

    @property
    def all_children(self):
        """Return exclusive children (via parent_id) + shared children (via assignments)."""
        exclusive = list(self.children.all())
        shared = [a.child for a in self.shared_child_assignments.all()]
        seen = {c.id for c in exclusive}
        for c in shared:
            if c.id not in seen:
                exclusive.append(c)
                seen.add(c.id)
        return exclusive

    @property
    def all_parents(self):
        """Return all parents: primary (via parent_id) + shared (via assignments)."""
        parents = []
        if self.parent:
            parents.append(self.parent)
        seen = {p.id for p in parents}
        for a in self.shared_parent_assignments.all():
            if a.parent_id not in seen:
                parents.append(a.parent)
                seen.add(a.parent_id)
        return parents

    @property
    def max_concurrent_bookings(self):
        """Max concurrent bookings based on the minimum slot allocation across shared children.

        If the testbed has no shared-child assignments, returns 1 (standard single booking).
        """
        assignments = self.shared_child_assignments.all()
        if not assignments:
            return 1
        return min(a.slots for a in assignments)

    def __repr__(self):
        return f'<Resource {self.name}>'


class ResourceAssignment(db.Model):
    """Many-to-many link between parent testbeds and shared child resources.

    Each assignment has a `slots` count controlling how many concurrent bookings
    of the parent testbed are allowed. A testbed's effective capacity is the
    minimum `slots` value across all its shared-child assignments (defaults to 1
    when there are no shared children).
    """
    __tablename__ = 'resource_assignments'

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    child_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    slots = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint('parent_id', 'child_id', name='uq_assignment_parent_child'),)

    parent = db.relationship('Resource', foreign_keys=[parent_id],
                             backref=db.backref('shared_child_assignments', lazy='dynamic',
                                                cascade='all, delete-orphan'))
    child = db.relationship('Resource', foreign_keys=[child_id],
                            backref=db.backref('shared_parent_assignments', lazy='dynamic',
                                               cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<ResourceAssignment parent={self.parent_id} child={self.child_id} slots={self.slots}>'


class Vlan(db.Model):
    __tablename__ = 'vlans'

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False, default='')
    description = db.Column(db.Text, default='')

    subnets = db.relationship('Subnet', backref='vlan', lazy='dynamic',
                              order_by='Subnet.cidr',
                              cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Vlan {self.number} {self.name}>'


class Subnet(db.Model):
    __tablename__ = 'subnets'

    id = db.Column(db.Integer, primary_key=True)
    vlan_id = db.Column(db.Integer, db.ForeignKey('vlans.id'), nullable=False, index=True)
    cidr = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False, default='')
    gateway = db.Column(db.String(45), nullable=True)
    description = db.Column(db.Text, default='')

    hosts = db.relationship('ResourceHost', backref='subnet', lazy='dynamic')

    @property
    def network(self):
        """Return an ipaddress.IPv4Network or IPv6Network object."""
        return ipaddress.ip_network(self.cidr, strict=False)

    @property
    def host_count(self):
        return self.hosts.count()

    def contains(self, addr):
        """Check if an IP address string falls within this subnet."""
        try:
            return ipaddress.ip_address(addr) in self.network
        except ValueError:
            return False

    def __repr__(self):
        return f'<Subnet {self.cidr}>'


def _resolve_to_ip(addr):
    """Try to parse addr as an IP; if it's a hostname, resolve via DNS."""
    import socket
    try:
        return ipaddress.ip_address(addr)
    except ValueError:
        pass
    # It's a hostname — try DNS resolution
    try:
        resolved = socket.gethostbyname(addr)
        return ipaddress.ip_address(resolved)
    except (socket.gaierror, ValueError):
        return None


def find_subnet_for_address(addr):
    """Find the matching Subnet for an IP address or hostname, or None."""
    ip = _resolve_to_ip(addr)
    if not ip:
        return None
    for subnet in Subnet.query.all():
        if ip in subnet.network:
            return subnet
    return None


class ResourceHost(db.Model):
    __tablename__ = 'resource_hosts'

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    address = db.Column(db.String(255), nullable=False)
    label = db.Column(db.String(100), nullable=False, default='')
    critical = db.Column(db.Boolean, nullable=False, default=True)
    subnet_id = db.Column(db.Integer, db.ForeignKey('subnets.id'), nullable=True, index=True)

    ping_results = db.relationship('PingResult', backref='host', lazy='dynamic',
                                   order_by='PingResult.checked_at.desc()',
                                   cascade='all, delete-orphan')

    def auto_link_subnet(self):
        """Auto-link this host to a matching subnet based on its IP address."""
        self.subnet_id = None
        subnet = find_subnet_for_address(self.address)
        if subnet:
            self.subnet_id = subnet.id

    # Number of consecutive failures before marking a host offline
    OFFLINE_THRESHOLD = 3

    @property
    def latest_ping(self):
        return self.ping_results.first()

    @property
    def ping_status(self):
        """Return 'online', 'offline', or 'unknown'.

        Requires OFFLINE_THRESHOLD consecutive failures before reporting
        offline, to avoid flapping on a single timeout.
        """
        recent_pings = self.ping_results.limit(self.OFFLINE_THRESHOLD).all()
        if not recent_pings:
            return 'unknown'
        if recent_pings[0].is_reachable:
            return 'online'
        # Latest ping failed — only report offline if the last N all failed
        if len(recent_pings) >= self.OFFLINE_THRESHOLD and all(
            not p.is_reachable for p in recent_pings
        ):
            return 'offline'
        # Not enough consecutive failures yet — still considered online
        return 'online'

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

    @staticmethod
    def user_has_active_booking(user_id, resource_id):
        """Check if user has a confirmed booking active right now for this resource."""
        now = datetime.now(timezone.utc)
        return Booking.query.filter(
            Booking.resource_id == resource_id,
            Booking.user_id == user_id,
            Booking.status == 'confirmed',
            Booking.start_time <= now,
            Booking.end_time >= now,
        ).first() is not None

    def has_conflict(self):
        """Check if this booking conflicts with existing confirmed bookings.

        Respects the resource's max_concurrent_bookings (driven by shared-child
        slot assignments).  A testbed with N slots allows up to N overlapping
        confirmed bookings before flagging a conflict.
        """
        overlapping = Booking.query.filter(
            Booking.resource_id == self.resource_id,
            Booking.status == 'confirmed',
            Booking.id != self.id,
            Booking.start_time < self.end_time,
            Booking.end_time > self.start_time,
        ).count()
        resource = db.session.get(Resource, self.resource_id)
        max_slots = resource.max_concurrent_bookings if resource else 1
        return overlapping >= max_slots

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


# ===== Tag model for resource labels =====
class Tag(db.Model):
    __tablename__ = 'tags'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True, index=True)
    color = db.Column(db.String(7), nullable=False, default='#6c757d')  # hex color

    resources = db.relationship('Resource', secondary=resource_tags, back_populates='tags')

    def __repr__(self):
        return f'<Tag {self.name}>'


# ===== Favorite resources =====
class Favorite(db.Model):
    __tablename__ = 'favorites'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('favorites', lazy='dynamic'))
    resource = db.relationship('Resource', backref=db.backref('favorited_by', lazy='dynamic'))

    __table_args__ = (db.UniqueConstraint('user_id', 'resource_id', name='uq_user_resource_fav'),)

    def __repr__(self):
        return f'<Favorite user={self.user_id} resource={self.resource_id}>'


# ===== Audit log =====
class AuditLog(db.Model):
    __tablename__ = 'audit_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False)  # e.g. 'booking.create', 'resource.delete'
    target_type = db.Column(db.String(50), nullable=True)  # e.g. 'booking', 'resource'
    target_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, nullable=True)  # JSON extra info
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = db.relationship('User', backref=db.backref('audit_logs', lazy='dynamic'))

    @staticmethod
    def log(action, target_type=None, target_id=None, details=None, user_id=None):
        """Create an audit log entry."""
        if details and not isinstance(details, str):
            details = json.dumps(details)
        entry = AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
        db.session.add(entry)

    def __repr__(self):
        return f'<AuditLog {self.action} @ {self.timestamp}>'


# ===== Maintenance Window =====
class MaintenanceWindow(db.Model):
    __tablename__ = 'maintenance_windows'

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    resource = db.relationship('Resource', backref=db.backref('maintenance_windows', lazy='dynamic'))
    creator = db.relationship('User')

    @property
    def is_active(self):
        now = datetime.now(timezone.utc)
        return self.start_time <= now <= self.end_time

    @staticmethod
    def resource_in_maintenance(resource_id):
        """Check if a resource currently has an active maintenance window."""
        now = datetime.now(timezone.utc)
        return MaintenanceWindow.query.filter(
            MaintenanceWindow.resource_id == resource_id,
            MaintenanceWindow.start_time <= now,
            MaintenanceWindow.end_time >= now,
        ).first() is not None

    def __repr__(self):
        return f'<MaintenanceWindow {self.title}>'


# ===== Alert Configuration =====
class AlertRule(db.Model):
    __tablename__ = 'alert_rules'

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    alert_type = db.Column(db.String(20), nullable=False, default='email')  # 'email' or 'webhook'
    target = db.Column(db.String(500), nullable=False)  # email address or webhook URL
    enabled = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_triggered = db.Column(db.DateTime, nullable=True)

    resource = db.relationship('Resource', backref=db.backref('alert_rules', lazy='dynamic'))
    creator = db.relationship('User')

    def __repr__(self):
        return f'<AlertRule {self.alert_type}:{self.target} for resource {self.resource_id}>'


# ===== Waitlist =====
class WaitlistEntry(db.Model):
    __tablename__ = 'waitlist_entries'

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    desired_start = db.Column(db.DateTime, nullable=False)
    desired_end = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='waiting')  # 'waiting', 'notified', 'expired'
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    notified_at = db.Column(db.DateTime, nullable=True)

    resource = db.relationship('Resource', backref=db.backref('waitlist_entries', lazy='dynamic'))
    user = db.relationship('User', backref=db.backref('waitlist_entries', lazy='dynamic'))

    def __repr__(self):
        return f'<WaitlistEntry user={self.user_id} resource={self.resource_id}>'


# ===== Access Points =====
_DEFAULT_PORTS = {'rdp': 3389, 'ssh': 22}


class AccessPoint(db.Model):
    __tablename__ = 'access_points'

    id = db.Column(db.Integer, primary_key=True)
    resource_id = db.Column(db.Integer, db.ForeignKey('resources.id'), nullable=False, index=True)
    protocol = db.Column(db.String(20), nullable=False, default='rdp')  # 'rdp' or 'ssh'
    hostname = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, nullable=True)  # NULL = protocol default
    username = db.Column(db.String(100), default='')
    _password = db.Column('password_b64', db.Text, default='')  # base64-encoded
    display_name = db.Column(db.String(100), default='')
    is_enabled = db.Column(db.Boolean, default=True)
    last_accessed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    last_accessed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    resource = db.relationship('Resource', backref=db.backref('access_points', lazy='dynamic',
                                                              cascade='all, delete-orphan'))
    last_user = db.relationship('User', foreign_keys=[last_accessed_by])

    @property
    def password(self):
        if not self._password:
            return ''
        try:
            return base64.b64decode(self._password.encode()).decode()
        except Exception:
            return self._password

    @password.setter
    def password(self, value):
        if value:
            self._password = base64.b64encode(value.encode()).decode()
        else:
            self._password = ''

    @property
    def effective_port(self):
        return self.port or _DEFAULT_PORTS.get(self.protocol, 0)

    @property
    def label(self):
        return self.display_name or f'{self.protocol.upper()} ({self.hostname})'

    def generate_ssh_command(self):
        """Generate the SSH command string."""
        cmd = 'ssh'
        if self.effective_port != 22:
            cmd += f' -p {self.effective_port}'
        if self.username:
            cmd += f' {self.username}@{self.hostname}'
        else:
            cmd += f' {self.hostname}'
        return cmd

    def __repr__(self):
        return f'<AccessPoint {self.protocol}://{self.hostname} for resource {self.resource_id}>'


def can_user_access(user, resource):
    """Check if user can access a resource's access points.

    Requires an active confirmed booking on the resource itself,
    or on any parent testbed (exclusive or shared).
    Admins always have access.
    """
    if user.is_admin:
        return True
    # Direct booking on this resource
    if Booking.user_has_active_booking(user.id, resource.id):
        return True
    # Exclusive parent
    if resource.parent_id and Booking.user_has_active_booking(user.id, resource.parent_id):
        return True
    # Shared parent testbeds
    for a in resource.shared_parent_assignments.all():
        if Booking.user_has_active_booking(user.id, a.parent_id):
            return True
    return False
