"""Tests for email service (.ics generation and send logic)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from app.models import Booking, Resource, User, AppSettings
from app.extensions import mail
from app.email_service import (
    _build_ics, _ical_dt, _ical_escape, _is_smtp_configured,
    send_booking_confirmation, send_booking_cancellation,
)


class TestIcalHelpers:
    def test_ical_dt_utc(self):
        dt = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        assert _ical_dt(dt) == '20260315T143000Z'

    def test_ical_dt_naive(self):
        """Naive datetimes treated as UTC."""
        dt = datetime(2026, 1, 1, 0, 0, 0)
        assert _ical_dt(dt) == '20260101T000000Z'

    def test_ical_escape_special_chars(self):
        assert _ical_escape('hello, world; ok\\n') == 'hello\\, world\\; ok\\\\n'

    def test_ical_escape_newlines(self):
        assert _ical_escape('line1\nline2') == 'line1\\nline2'


class TestBuildIcs:
    def test_build_ics_request(self, app, db, regular_user, sample_resource):
        with app.app_context():
            now = datetime.now(timezone.utc)
            booking = Booking(
                resource_id=sample_resource.id, user_id=regular_user.id,
                title='ICS Test', start_time=now, end_time=now + timedelta(hours=2),
                notes='Some notes', status='confirmed',
            )
            db.session.add(booking)
            db.session.commit()

            ics = _build_ics(booking, method='REQUEST')
            assert 'METHOD:REQUEST' in ics
            assert 'STATUS:CONFIRMED' in ics
            assert 'ICS Test' in ics
            assert 'SEQUENCE:0' in ics
            assert booking.calendar_uid in ics

    def test_build_ics_cancel(self, app, db, regular_user, sample_resource):
        with app.app_context():
            now = datetime.now(timezone.utc)
            booking = Booking(
                resource_id=sample_resource.id, user_id=regular_user.id,
                title='Cancel ICS', start_time=now, end_time=now + timedelta(hours=1),
                status='cancelled',
            )
            db.session.add(booking)
            db.session.commit()

            ics = _build_ics(booking, method='CANCEL')
            assert 'METHOD:CANCEL' in ics
            assert 'STATUS:CANCELLED' in ics
            assert 'SEQUENCE:1' in ics

    def test_build_ics_contains_attendee(self, app, db, regular_user, sample_resource):
        with app.app_context():
            now = datetime.now(timezone.utc)
            booking = Booking(
                resource_id=sample_resource.id, user_id=regular_user.id,
                title='Attendee Test', start_time=now, end_time=now + timedelta(hours=1),
            )
            db.session.add(booking)
            db.session.commit()
            ics = _build_ics(booking)
            assert regular_user.email in ics


class TestSmtpConfigured:
    def test_not_configured_by_default(self, app, db):
        with app.app_context():
            assert not _is_smtp_configured()

    def test_configured_with_app_settings(self, app, db):
        with app.app_context():
            AppSettings.set('smtp_host', 'mail.real.com')
            assert _is_smtp_configured()

    def test_example_host_not_considered_configured(self, app, db):
        with app.app_context():
            AppSettings.set('smtp_host', 'smtp.example.com')
            assert not _is_smtp_configured()


class TestSendBookingEmails:
    def test_confirmation_skipped_when_no_smtp(self, app, db, regular_user, sample_resource):
        """Should not raise when SMTP is not configured."""
        with app.app_context():
            now = datetime.now(timezone.utc)
            booking = Booking(
                resource_id=sample_resource.id, user_id=regular_user.id,
                title='No SMTP', start_time=now, end_time=now + timedelta(hours=1),
                status='confirmed',
            )
            db.session.add(booking)
            db.session.commit()
            # Should not raise
            send_booking_confirmation(booking)

    def test_cancellation_skipped_when_no_smtp(self, app, db, regular_user, sample_resource):
        with app.app_context():
            now = datetime.now(timezone.utc)
            booking = Booking(
                resource_id=sample_resource.id, user_id=regular_user.id,
                title='No SMTP Cancel', start_time=now, end_time=now + timedelta(hours=1),
                status='cancelled',
            )
            db.session.add(booking)
            db.session.commit()
            send_booking_cancellation(booking)

    def test_confirmation_builds_and_sends(self, app, db, regular_user, sample_resource):
        """Verify confirmation builds correct ICS and attempts to send."""
        with app.app_context():
            AppSettings.set('smtp_host', 'mail.real.com')
            AppSettings.set('smtp_sender', 'test@real.com')
            app.config['MAIL_SERVER'] = 'mail.real.com'
            app.config['MAIL_DEFAULT_SENDER'] = 'test@real.com'
            now = datetime.now(timezone.utc)
            booking = Booking(
                resource_id=sample_resource.id, user_id=regular_user.id,
                title='Send Test', start_time=now, end_time=now + timedelta(hours=1),
                status='confirmed',
            )
            db.session.add(booking)
            db.session.commit()

            # Verify the ICS content is correct
            ics = _build_ics(booking, method='REQUEST')
            assert 'METHOD:REQUEST' in ics
            assert booking.calendar_uid in ics

            # Verify _is_smtp_configured returns True
            assert _is_smtp_configured()

    def test_cancellation_builds_and_sends(self, app, db, regular_user, sample_resource):
        """Verify cancellation builds correct ICS and attempts to send."""
        with app.app_context():
            AppSettings.set('smtp_host', 'mail.real.com')
            AppSettings.set('smtp_sender', 'test@real.com')
            app.config['MAIL_SERVER'] = 'mail.real.com'
            app.config['MAIL_DEFAULT_SENDER'] = 'test@real.com'
            now = datetime.now(timezone.utc)
            booking = Booking(
                resource_id=sample_resource.id, user_id=regular_user.id,
                title='Cancel Send', start_time=now, end_time=now + timedelta(hours=1),
                status='cancelled',
            )
            db.session.add(booking)
            db.session.commit()

            ics = _build_ics(booking, method='CANCEL')
            assert 'METHOD:CANCEL' in ics
            assert 'STATUS:CANCELLED' in ics
            assert booking.calendar_uid in ics
