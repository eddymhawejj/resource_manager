"""Tests for booking routes and conflict detection."""

from datetime import datetime, timedelta, timezone

from tests.conftest import login
from app.models import Booking, Resource


class TestBookingList:
    def test_list_bookings_authenticated(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.get('/bookings/')
        assert resp.status_code == 200

    def test_list_bookings_unauthenticated(self, client):
        resp = client.get('/bookings/')
        assert resp.status_code in (302, 401)


class TestBookingCreate:
    def test_create_booking_form_renders(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.get('/bookings/create')
        assert resp.status_code == 200

    def test_create_booking_success(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc) + timedelta(hours=1)
        end = now + timedelta(hours=2)
        resp = client.post('/bookings/create', data={
            'resource_id': sample_resource.id,
            'title': 'Test Booking',
            'start_time': now.strftime('%Y-%m-%dT%H:%M'),
            'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            'notes': 'Test notes',
            'all_day': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        booking = Booking.query.filter_by(title='Test Booking').first()
        assert booking is not None
        assert booking.status == 'confirmed'
        assert booking.user_id == regular_user.id

    def test_create_booking_conflict_blocked(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc) + timedelta(hours=1)
        end = now + timedelta(hours=3)

        # Create an existing booking
        existing = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Existing', start_time=now, end_time=end, status='confirmed',
        )
        db.session.add(existing)
        db.session.commit()

        # Try to create overlapping booking
        resp = client.post('/bookings/create', data={
            'resource_id': sample_resource.id,
            'title': 'Conflicting',
            'start_time': (now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'end_time': (end + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'notes': '',
            'all_day': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'conflict' in resp.data.lower()
        assert Booking.query.filter_by(title='Conflicting').first() is None


class TestBookingCancel:
    def test_cancel_own_booking(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Cancel Me', start_time=now, end_time=now + timedelta(hours=1),
            status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        bid = b.id

        resp = client.post(f'/bookings/{bid}/cancel', follow_redirects=True)
        assert resp.status_code == 200
        db.session.refresh(b)
        assert b.status == 'cancelled'

    def test_cancel_others_booking_forbidden(self, client, regular_user, admin_user, sample_resource, db):
        # Admin creates a booking
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=admin_user.id,
            title='Admin Booking', start_time=now, end_time=now + timedelta(hours=1),
            status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        bid = b.id

        # Regular user tries to cancel it
        login(client, 'testuser', 'userpass')
        resp = client.post(f'/bookings/{bid}/cancel')
        assert resp.status_code == 403

    def test_admin_can_cancel_any_booking(self, client, admin_user, regular_user, sample_resource, db):
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='User Booking', start_time=now, end_time=now + timedelta(hours=1),
            status='confirmed',
        )
        db.session.add(b)
        db.session.commit()
        bid = b.id

        login(client, 'admin', 'adminpass')
        resp = client.post(f'/bookings/{bid}/cancel', follow_redirects=True)
        assert resp.status_code == 200
        db.session.refresh(b)
        assert b.status == 'cancelled'


class TestCalendarEvents:
    def test_events_json(self, client, regular_user, sample_resource, db):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc)
        b = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Event Test', start_time=now, end_time=now + timedelta(hours=1),
            status='confirmed',
        )
        db.session.add(b)
        db.session.commit()

        resp = client.get('/bookings/events')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert 'Event Test' in data[0]['title']
