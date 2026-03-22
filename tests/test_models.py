"""Tests for SQLAlchemy models and business logic."""

from datetime import datetime, timedelta, timezone

from app.models import (
    User, Resource, ResourceHost, Booking, PingResult,
    Vlan, Subnet, AppSettings, Tag, Favorite, AccessPoint,
    MaintenanceWindow, ResourceAssignment, AuditLog, ResourceGroup,
    can_user_access,
)


class TestUserModel:
    def test_set_and_check_password(self, db):
        user = User(username='pw_test', email='pw@test.com', display_name='PW')
        user.set_password('secret123')
        assert user.check_password('secret123')
        assert not user.check_password('wrong')

    def test_check_password_no_hash(self, db):
        user = User(username='nohash', email='no@test.com', display_name='No')
        assert not user.check_password('anything')

    def test_is_admin(self, db):
        admin = User(username='a', email='a@t.com', role='admin')
        regular = User(username='b', email='b@t.com', role='user')
        assert admin.is_admin
        assert not regular.is_admin

    def test_repr(self, db):
        user = User(username='repr_test', email='r@t.com')
        assert 'repr_test' in repr(user)


class TestResourceModel:
    def test_is_testbed(self, db, sample_resource):
        assert sample_resource.is_testbed
        child = Resource(name='Child', resource_type='testbed', parent_id=sample_resource.id)
        db.session.add(child)
        db.session.commit()
        assert not child.is_testbed

    def test_status_no_hosts_no_children(self, db, sample_resource):
        assert sample_resource.status == 'unknown'

    def test_status_critical_host_online(self, db, sample_resource, sample_host):
        # Add successful pings
        for _ in range(3):
            db.session.add(PingResult(
                host_id=sample_host.id, is_reachable=True, response_time_ms=5.0,
            ))
        db.session.commit()
        assert sample_resource.status == 'online'

    def test_status_critical_host_offline(self, db, sample_resource, sample_host):
        # 3 consecutive failures = offline
        for _ in range(3):
            db.session.add(PingResult(
                host_id=sample_host.id, is_reachable=False,
            ))
        db.session.commit()
        assert sample_resource.status == 'offline'

    def test_status_non_critical_host_ignored(self, db, sample_resource):
        host = ResourceHost(
            resource_id=sample_resource.id, address='10.0.0.1',
            label='Non-critical', critical=False,
        )
        db.session.add(host)
        db.session.commit()
        # Non-critical hosts don't affect status → unknown
        assert sample_resource.status == 'unknown'

    def test_status_degraded(self, db, sample_resource):
        h1 = ResourceHost(resource_id=sample_resource.id, address='10.0.0.1', label='H1', critical=True)
        h2 = ResourceHost(resource_id=sample_resource.id, address='10.0.0.2', label='H2', critical=True)
        db.session.add_all([h1, h2])
        db.session.commit()
        # h1 online
        db.session.add(PingResult(host_id=h1.id, is_reachable=True))
        # h2 offline (3 failures)
        for _ in range(3):
            db.session.add(PingResult(host_id=h2.id, is_reachable=False))
        db.session.commit()
        assert sample_resource.status == 'degraded'

    def test_is_reachable_property(self, db, sample_resource, sample_host):
        for _ in range(3):
            db.session.add(PingResult(host_id=sample_host.id, is_reachable=True))
        db.session.commit()
        assert sample_resource.is_reachable is True

    def test_max_concurrent_bookings_default(self, db, sample_resource):
        assert sample_resource.max_concurrent_bookings == 1

    def test_max_concurrent_bookings_with_assignments(self, db, sample_resource):
        child = Resource(name='Shared Child', resource_type='testbed')
        db.session.add(child)
        db.session.commit()
        a = ResourceAssignment(parent_id=sample_resource.id, child_id=child.id, slots=3)
        db.session.add(a)
        db.session.commit()
        assert sample_resource.max_concurrent_bookings == 3


class TestResourceHostModel:
    def test_ping_status_unknown_no_pings(self, db, sample_host):
        assert sample_host.ping_status == 'unknown'

    def test_ping_status_online(self, db, sample_host):
        db.session.add(PingResult(host_id=sample_host.id, is_reachable=True))
        db.session.commit()
        assert sample_host.ping_status == 'online'

    def test_ping_status_single_failure_stays_online(self, db, sample_host):
        """One failure shouldn't flip to offline — need 3 consecutive."""
        db.session.add(PingResult(host_id=sample_host.id, is_reachable=False))
        db.session.commit()
        assert sample_host.ping_status == 'online'

    def test_ping_status_offline_after_threshold(self, db, sample_host):
        for _ in range(3):
            db.session.add(PingResult(host_id=sample_host.id, is_reachable=False))
        db.session.commit()
        assert sample_host.ping_status == 'offline'

    def test_latest_ping(self, db, sample_host):
        p = PingResult(host_id=sample_host.id, is_reachable=True, response_time_ms=12.5)
        db.session.add(p)
        db.session.commit()
        assert sample_host.latest_ping.response_time_ms == 12.5


class TestBookingModel:
    def test_has_conflict_no_overlap(self, db, sample_resource, regular_user):
        now = datetime.now(timezone.utc)
        b1 = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='First', start_time=now, end_time=now + timedelta(hours=1),
            status='confirmed',
        )
        db.session.add(b1)
        db.session.commit()

        b2 = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Second', start_time=now + timedelta(hours=2), end_time=now + timedelta(hours=3),
            status='confirmed',
        )
        assert not b2.has_conflict()

    def test_has_conflict_with_overlap(self, db, sample_resource, regular_user):
        now = datetime.now(timezone.utc)
        b1 = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='First', start_time=now, end_time=now + timedelta(hours=2),
            status='confirmed',
        )
        db.session.add(b1)
        db.session.commit()

        b2 = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Second', start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=3),
            status='confirmed',
        )
        assert b2.has_conflict()

    def test_conflict_respects_max_slots(self, db, regular_user):
        """With 2 slots, one overlap is fine but two is a conflict."""
        parent = Resource(name='Multi-slot', resource_type='testbed')
        child = Resource(name='Shared', resource_type='testbed')
        db.session.add_all([parent, child])
        db.session.commit()
        a = ResourceAssignment(parent_id=parent.id, child_id=child.id, slots=2)
        db.session.add(a)
        db.session.commit()

        now = datetime.now(timezone.utc)
        b1 = Booking(
            resource_id=parent.id, user_id=regular_user.id,
            title='Slot 1', start_time=now, end_time=now + timedelta(hours=2),
            status='confirmed',
        )
        db.session.add(b1)
        db.session.commit()

        # Second booking overlaps but fits in slot 2
        b2 = Booking(
            resource_id=parent.id, user_id=regular_user.id,
            title='Slot 2', start_time=now, end_time=now + timedelta(hours=2),
            status='confirmed',
        )
        assert not b2.has_conflict()

        # Add second booking, now a third should conflict
        db.session.add(b2)
        db.session.commit()
        b3 = Booking(
            resource_id=parent.id, user_id=regular_user.id,
            title='Slot 3', start_time=now, end_time=now + timedelta(hours=2),
            status='confirmed',
        )
        assert b3.has_conflict()

    def test_cancelled_booking_no_conflict(self, db, sample_resource, regular_user):
        now = datetime.now(timezone.utc)
        b1 = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Cancelled', start_time=now, end_time=now + timedelta(hours=2),
            status='cancelled',
        )
        db.session.add(b1)
        db.session.commit()

        b2 = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='New', start_time=now, end_time=now + timedelta(hours=2),
            status='confirmed',
        )
        assert not b2.has_conflict()

    def test_ensure_calendar_uid(self, db, sample_resource, regular_user):
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='UID Test', start_time=now, end_time=now + timedelta(hours=1),
        )
        uid = b.ensure_calendar_uid()
        assert uid is not None
        assert b.ensure_calendar_uid() == uid  # idempotent

    def test_user_has_active_booking(self, db, sample_resource, regular_user):
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Active', start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=1),
            status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        assert Booking.user_has_active_booking(regular_user.id, sample_resource.id)

    def test_user_has_no_active_booking_future(self, db, sample_resource, regular_user):
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Future', start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=2),
            status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        assert not Booking.user_has_active_booking(regular_user.id, sample_resource.id)


class TestSubnetModel:
    def test_contains(self, db):
        vlan = Vlan(number=100, name='Test VLAN')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='192.168.1.0/24', name='Test Subnet')
        db.session.add(subnet)
        db.session.commit()
        assert subnet.contains('192.168.1.50')
        assert not subnet.contains('10.0.0.1')
        assert not subnet.contains('not-an-ip')

    def test_network_property(self, db):
        vlan = Vlan(number=200, name='VLAN 200')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='10.0.0.0/16', name='Large')
        db.session.add(subnet)
        db.session.commit()
        import ipaddress
        assert subnet.network == ipaddress.ip_network('10.0.0.0/16')

    def test_host_count(self, db, sample_resource):
        vlan = Vlan(number=300, name='V300')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='172.16.0.0/24', name='S300')
        db.session.add(subnet)
        db.session.commit()
        h = ResourceHost(resource_id=sample_resource.id, address='172.16.0.5', label='H', subnet_id=subnet.id)
        db.session.add(h)
        db.session.commit()
        assert subnet.host_count == 1


class TestAccessPointModel:
    def test_password_encoding(self, db, sample_resource):
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='rdp',
            hostname='192.168.1.10',
        )
        ap.password = 'my_secret'
        assert ap.password == 'my_secret'
        assert ap._password != 'my_secret'  # stored as base64

    def test_effective_port_defaults(self, db, sample_resource):
        rdp = AccessPoint(resource_id=sample_resource.id, protocol='rdp', hostname='h')
        ssh = AccessPoint(resource_id=sample_resource.id, protocol='ssh', hostname='h')
        custom = AccessPoint(resource_id=sample_resource.id, protocol='rdp', hostname='h', port=9999)
        assert rdp.effective_port == 3389
        assert ssh.effective_port == 22
        assert custom.effective_port == 9999

    def test_label_with_display_name(self, db, sample_resource):
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='ssh',
            hostname='server1', display_name='My Server',
        )
        assert ap.label == 'My Server'

    def test_label_without_display_name(self, db, sample_resource):
        ap = AccessPoint(resource_id=sample_resource.id, protocol='ssh', hostname='server1')
        assert ap.label == 'SSH (server1)'

    def test_generate_ssh_command(self, db, sample_resource):
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='ssh',
            hostname='server1', username='root',
        )
        assert ap.generate_ssh_command() == 'ssh root@server1'

    def test_generate_ssh_command_custom_port(self, db, sample_resource):
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='ssh',
            hostname='server1', port=2222, username='admin',
        )
        assert ap.generate_ssh_command() == 'ssh -p 2222 admin@server1'


class TestAppSettings:
    def test_get_default(self, db):
        assert AppSettings.get('nonexistent', 'fallback') == 'fallback'

    def test_set_and_get(self, db):
        AppSettings.set('test_key', 'test_value')
        assert AppSettings.get('test_key') == 'test_value'

    def test_set_overwrites(self, db):
        AppSettings.set('key', 'value1')
        AppSettings.set('key', 'value2')
        assert AppSettings.get('key') == 'value2'


class TestMaintenanceWindow:
    def test_is_active(self, db, sample_resource):
        now = datetime.now(timezone.utc)
        active = MaintenanceWindow(
            resource_id=sample_resource.id, title='Active',
            start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=1),
        )
        inactive = MaintenanceWindow(
            resource_id=sample_resource.id, title='Past',
            start_time=now - timedelta(hours=5), end_time=now - timedelta(hours=3),
        )
        assert active.is_active
        assert not inactive.is_active

    def test_resource_in_maintenance(self, db, sample_resource):
        now = datetime.now(timezone.utc)
        mw = MaintenanceWindow(
            resource_id=sample_resource.id, title='MW',
            start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=1),
        )
        db.session.add(mw)
        db.session.commit()
        assert MaintenanceWindow.resource_in_maintenance(sample_resource.id)


class TestAuditLog:
    def test_log_creates_entry(self, db, admin_user):
        AuditLog.log('test.action', 'resource', 1, {'key': 'val'}, user_id=admin_user.id)
        db.session.commit()
        entry = AuditLog.query.first()
        assert entry.action == 'test.action'
        assert entry.target_type == 'resource'
        assert '"key"' in entry.details


class TestCanUserAccess:
    def test_admin_always_has_access(self, db, admin_user, sample_resource):
        assert can_user_access(admin_user, sample_resource)

    def test_user_without_booking_denied(self, db, regular_user, sample_resource):
        assert not can_user_access(regular_user, sample_resource)

    def test_user_with_active_booking_granted(self, db, regular_user, sample_resource):
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Access Test', start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1), status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        assert can_user_access(regular_user, sample_resource)

    def test_user_with_parent_booking_granted(self, db, regular_user, sample_resource):
        child = Resource(name='Child', resource_type='testbed', parent_id=sample_resource.id)
        db.session.add(child)
        db.session.commit()
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Parent Booking', start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1), status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        assert can_user_access(regular_user, child)


class TestResourceGroup:
    def test_create_group(self, db):
        group = ResourceGroup(name='Team A', description='Test team', ldap_dn='CN=TeamA,OU=Groups,DC=example,DC=com')
        db.session.add(group)
        db.session.commit()
        assert group.id is not None
        assert group.name == 'Team A'
        assert group.ldap_dn == 'CN=TeamA,OU=Groups,DC=example,DC=com'

    def test_group_members(self, db):
        group = ResourceGroup(name='Team B')
        user = User(username='member1', email='m1@t.com', display_name='Member 1', role='user')
        user.set_password('test123')
        db.session.add_all([group, user])
        db.session.commit()
        group.members.append(user)
        db.session.commit()
        assert user in group.members
        assert group in user.resource_groups

    def test_group_resources(self, db):
        group = ResourceGroup(name='Team C')
        resource = Resource(name='Restricted TB', resource_type='testbed')
        db.session.add_all([group, resource])
        db.session.commit()
        group.resources.append(resource)
        db.session.commit()
        assert resource in group.resources
        assert group in resource.access_groups


class TestResourceVisibility:
    def test_no_groups_visible_to_all(self, db, regular_user, sample_resource):
        """Resources without access groups are visible to everyone."""
        assert sample_resource.is_visible_to(regular_user)

    def test_admin_always_visible(self, db, admin_user, sample_resource):
        """Admins can see group-restricted resources."""
        group = ResourceGroup(name='Restricted')
        db.session.add(group)
        db.session.commit()
        sample_resource.access_groups = [group]
        db.session.commit()
        assert sample_resource.is_visible_to(admin_user)

    def test_non_member_cannot_see(self, db, regular_user, sample_resource):
        """Users not in the group cannot see restricted resources."""
        group = ResourceGroup(name='Secret Team')
        db.session.add(group)
        db.session.commit()
        sample_resource.access_groups = [group]
        db.session.commit()
        assert not sample_resource.is_visible_to(regular_user)

    def test_member_can_see(self, db, regular_user, sample_resource):
        """Users in the group can see restricted resources."""
        group = ResourceGroup(name='My Team')
        db.session.add(group)
        db.session.commit()
        group.members.append(regular_user)
        sample_resource.access_groups = [group]
        db.session.commit()
        assert sample_resource.is_visible_to(regular_user)

    def test_child_inherits_parent_groups(self, db, regular_user, sample_resource):
        """Child resources inherit parent's group restrictions."""
        group = ResourceGroup(name='Parent Group')
        db.session.add(group)
        db.session.commit()
        sample_resource.access_groups = [group]
        child = Resource(name='Child', resource_type='server', parent_id=sample_resource.id)
        db.session.add(child)
        db.session.commit()
        # Not a member — child should be hidden too
        assert not child.is_visible_to(regular_user)
        # Add to group — child should be visible
        group.members.append(regular_user)
        db.session.commit()
        assert child.is_visible_to(regular_user)

    def test_can_user_access_blocked_by_group(self, db, regular_user, sample_resource):
        """can_user_access returns False if user is not in required group, even with booking."""
        group = ResourceGroup(name='Gatekeep')
        db.session.add(group)
        db.session.commit()
        sample_resource.access_groups = [group]
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Blocked', start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1), status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        assert not can_user_access(regular_user, sample_resource)


class TestAccessPointGroupRestriction:
    def test_no_group_visible_to_all(self, db, regular_user, sample_resource):
        """Access points without a required group are visible to everyone."""
        ap = AccessPoint(resource_id=sample_resource.id, protocol='rdp', hostname='10.0.0.1')
        db.session.add(ap)
        db.session.commit()
        assert ap.is_visible_to(regular_user)

    def test_admin_always_visible(self, db, admin_user, sample_resource):
        """Admins can see group-restricted access points."""
        group = ResourceGroup(name='Admin AP Group')
        db.session.add(group)
        db.session.commit()
        ap = AccessPoint(resource_id=sample_resource.id, protocol='rdp', hostname='10.0.0.1',
                         required_group_id=group.id)
        db.session.add(ap)
        db.session.commit()
        assert ap.is_visible_to(admin_user)

    def test_non_member_cannot_see(self, db, regular_user, sample_resource):
        """Users not in the required group cannot see the access point."""
        group = ResourceGroup(name='Privileged')
        db.session.add(group)
        db.session.commit()
        ap = AccessPoint(resource_id=sample_resource.id, protocol='rdp', hostname='10.0.0.1',
                         required_group_id=group.id)
        db.session.add(ap)
        db.session.commit()
        assert not ap.is_visible_to(regular_user)

    def test_member_can_see(self, db, regular_user, sample_resource):
        """Users in the required group can see the access point."""
        group = ResourceGroup(name='Ops Team')
        db.session.add(group)
        db.session.commit()
        group.members.append(regular_user)
        ap = AccessPoint(resource_id=sample_resource.id, protocol='rdp', hostname='10.0.0.1',
                         required_group_id=group.id)
        db.session.add(ap)
        db.session.commit()
        assert ap.is_visible_to(regular_user)
