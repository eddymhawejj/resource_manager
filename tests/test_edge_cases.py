"""Edge case and validation tests."""

from datetime import datetime, timedelta, timezone

from tests.conftest import login
from app.models import (
    Booking, Resource, ResourceHost, Vlan, Subnet,
    User, Tag, Favorite, WaitlistEntry, ResourceAssignment,
    AccessPoint, PingResult,
)


class TestBookingEdgeCases:
    def test_start_equals_end_rejected(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc) + timedelta(hours=1)
        resp = client.post('/bookings/create', data={
            'resource_id': sample_resource.id,
            'title': 'Zero Duration',
            'start_time': now.strftime('%Y-%m-%dT%H:%M'),
            'end_time': now.strftime('%Y-%m-%dT%H:%M'),
            'notes': '',
            'all_day': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'after start' in resp.data.lower() or Booking.query.filter_by(title='Zero Duration').first() is None

    def test_end_before_start_rejected(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc) + timedelta(hours=2)
        earlier = now - timedelta(hours=1)
        resp = client.post('/bookings/create', data={
            'resource_id': sample_resource.id,
            'title': 'Backwards',
            'start_time': now.strftime('%Y-%m-%dT%H:%M'),
            'end_time': earlier.strftime('%Y-%m-%dT%H:%M'),
            'notes': '',
            'all_day': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert Booking.query.filter_by(title='Backwards').first() is None

    def test_booking_during_maintenance_blocked(self, client, regular_user, sample_resource, db):
        from app.models import MaintenanceWindow
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc)
        mw = MaintenanceWindow(
            resource_id=sample_resource.id, title='Blocked',
            start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=5),
        )
        db.session.add(mw)
        db.session.commit()

        resp = client.post('/bookings/create', data={
            'resource_id': sample_resource.id,
            'title': 'During MW',
            'start_time': (now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'end_time': (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
            'notes': '',
            'all_day': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'maintenance' in resp.data.lower()
        assert Booking.query.filter_by(title='During MW').first() is None

    def test_cancel_nonexistent_booking_404(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.post('/bookings/99999/cancel')
        assert resp.status_code == 404


class TestVlanEdgeCases:
    def test_duplicate_vlan_number_rejected(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        vlan = Vlan(number=42, name='First')
        db.session.add(vlan)
        db.session.commit()
        resp = client.post('/network/vlans/add', data={
            'number': 42, 'name': 'Duplicate',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert Vlan.query.filter_by(number=42).count() == 1

    def test_vlan_number_out_of_range(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        resp = client.post('/network/vlans/add', data={
            'number': 5000, 'name': 'Invalid',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert Vlan.query.filter_by(number=5000).first() is None


class TestSubnetEdgeCases:
    def test_invalid_cidr_rejected(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        vlan = Vlan(number=800, name='V800')
        db.session.add(vlan)
        db.session.commit()
        resp = client.post('/network/subnets/add', data={
            'vlan_id': vlan.id,
            'cidr': 'not-a-cidr',
            'name': 'Bad',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert Subnet.query.filter_by(name='Bad').first() is None

    def test_duplicate_cidr_rejected(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        vlan = Vlan(number=801, name='V801')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='10.99.0.0/24', name='First')
        db.session.add(subnet)
        db.session.commit()
        resp = client.post('/network/subnets/add', data={
            'vlan_id': vlan.id,
            'cidr': '10.99.0.0/24',
            'name': 'Dup',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert Subnet.query.filter_by(cidr='10.99.0.0/24').count() == 1


class TestResourceEdgeCases:
    def test_resource_detail_nonexistent_404(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.get('/resources/99999')
        assert resp.status_code == 404

    def test_delete_resource_cascade_hosts(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        r = Resource(name='Cascade Test', resource_type='testbed')
        db.session.add(r)
        db.session.commit()
        h = ResourceHost(resource_id=r.id, address='1.2.3.4', label='H', critical=True)
        db.session.add(h)
        db.session.commit()
        rid, hid = r.id, h.id
        client.post(f'/resources/{rid}/delete', follow_redirects=True)
        assert db.session.get(Resource, rid) is None
        assert db.session.get(ResourceHost, hid) is None

    def test_delete_resource_cascade_bookings(self, client, admin_user, regular_user, db):
        login(client, 'admin', 'adminpass')
        r = Resource(name='Cascade Booking', resource_type='testbed')
        db.session.add(r)
        db.session.commit()
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=r.id, user_id=regular_user.id,
            title='Gone', start_time=now, end_time=now + timedelta(hours=1),
        )
        db.session.add(b)
        db.session.commit()
        rid, bid = r.id, b.id
        client.post(f'/resources/{rid}/delete', follow_redirects=True)
        assert db.session.get(Booking, bid) is None


class TestFavorites:
    def test_toggle_favorite(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        # Add favorite
        resp = client.post(f'/resources/{sample_resource.id}/favorite', follow_redirects=True)
        assert resp.status_code == 200
        fav = Favorite.query.filter_by(user_id=regular_user.id, resource_id=sample_resource.id).first()
        assert fav is not None

        # Toggle off
        resp = client.post(f'/resources/{sample_resource.id}/favorite', follow_redirects=True)
        assert resp.status_code == 200
        fav = Favorite.query.filter_by(user_id=regular_user.id, resource_id=sample_resource.id).first()
        assert fav is None


class TestWaitlist:
    def test_add_to_waitlist(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc) + timedelta(hours=1)
        resp = client.post('/bookings/waitlist/add', data={
            'resource_id': sample_resource.id,
            'desired_start': now.isoformat(),
            'desired_end': (now + timedelta(hours=2)).isoformat(),
            'notes': 'Please',
        }, follow_redirects=True)
        assert resp.status_code == 200
        entry = WaitlistEntry.query.filter_by(user_id=regular_user.id).first()
        assert entry is not None

    def test_remove_from_waitlist(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc)
        entry = WaitlistEntry(
            resource_id=sample_resource.id, user_id=regular_user.id,
            desired_start=now, desired_end=now + timedelta(hours=1),
        )
        db.session.add(entry)
        db.session.commit()
        eid = entry.id
        resp = client.post(f'/bookings/waitlist/{eid}/remove', follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(WaitlistEntry, eid) is None

    def test_remove_others_waitlist_forbidden(self, client, regular_user, admin_user, sample_resource, db):
        now = datetime.now(timezone.utc)
        entry = WaitlistEntry(
            resource_id=sample_resource.id, user_id=admin_user.id,
            desired_start=now, desired_end=now + timedelta(hours=1),
        )
        db.session.add(entry)
        db.session.commit()
        login(client, 'testuser', 'userpass')
        resp = client.post(f'/bookings/waitlist/{entry.id}/remove')
        assert resp.status_code == 403


class TestResourceAssignmentEdgeCases:
    def test_max_concurrent_with_multiple_assignments(self, db):
        parent = Resource(name='Multi-assign', resource_type='testbed')
        c1 = Resource(name='Child1', resource_type='testbed')
        c2 = Resource(name='Child2', resource_type='testbed')
        db.session.add_all([parent, c1, c2])
        db.session.commit()
        a1 = ResourceAssignment(parent_id=parent.id, child_id=c1.id, slots=5)
        a2 = ResourceAssignment(parent_id=parent.id, child_id=c2.id, slots=3)
        db.session.add_all([a1, a2])
        db.session.commit()
        # Max concurrent = min(5, 3) = 3
        assert parent.max_concurrent_bookings == 3

    def test_all_children_includes_shared(self, db):
        parent = Resource(name='Parent', resource_type='testbed')
        exclusive = Resource(name='Exclusive', resource_type='testbed')
        shared = Resource(name='Shared', resource_type='testbed')
        db.session.add_all([parent, exclusive, shared])
        db.session.commit()
        exclusive.parent_id = parent.id
        a = ResourceAssignment(parent_id=parent.id, child_id=shared.id, slots=1)
        db.session.add(a)
        db.session.commit()
        children = parent.all_children
        child_names = {c.name for c in children}
        assert 'Exclusive' in child_names
        assert 'Shared' in child_names


class TestPingStatusEdgeCases:
    def test_two_failures_stays_online(self, db, sample_host):
        """Need 3 consecutive failures for offline, 2 should stay online."""
        for _ in range(2):
            db.session.add(PingResult(host_id=sample_host.id, is_reachable=False))
        db.session.commit()
        assert sample_host.ping_status == 'online'

    def test_recovery_after_offline(self, db, sample_host):
        """One success after 3 failures should flip back to online."""
        for _ in range(3):
            db.session.add(PingResult(host_id=sample_host.id, is_reachable=False))
        db.session.commit()
        assert sample_host.ping_status == 'offline'
        # Now add a success
        db.session.add(PingResult(host_id=sample_host.id, is_reachable=True))
        db.session.commit()
        assert sample_host.ping_status == 'online'


class TestAccessPointEdgeCases:
    def test_empty_password(self, db, sample_resource):
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='rdp', hostname='h',
        )
        ap.password = ''
        assert ap.password == ''
        assert ap._password == ''

    def test_ssh_command_no_username(self, db, sample_resource):
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='ssh',
            hostname='server1',
        )
        assert ap.generate_ssh_command() == 'ssh server1'
