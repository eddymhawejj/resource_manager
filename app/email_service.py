import logging
from datetime import timezone
from email.mime.base import MIMEBase

from flask import current_app
from flask_mail import Message

from app.extensions import mail
from app.models import AppSettings

logger = logging.getLogger(__name__)


def _is_smtp_configured():
    """Check if SMTP is configured (via AppSettings or env)."""
    smtp_host = AppSettings.get('smtp_host', current_app.config.get('MAIL_SERVER', ''))
    return smtp_host and smtp_host != 'smtp.example.com'


def _update_mail_config():
    """Update Flask-Mail config from AppSettings at runtime."""
    app = current_app._get_current_object()

    smtp_host = AppSettings.get('smtp_host', '')
    if smtp_host:
        app.config['MAIL_SERVER'] = smtp_host
        app.config['MAIL_PORT'] = int(AppSettings.get('smtp_port', '587'))
        app.config['MAIL_USE_TLS'] = AppSettings.get('smtp_use_tls', 'true') == 'true'
        app.config['MAIL_USERNAME'] = AppSettings.get('smtp_username', '')
        app.config['MAIL_PASSWORD'] = AppSettings.get('smtp_password', '')
        app.config['MAIL_DEFAULT_SENDER'] = AppSettings.get('smtp_sender', 'noreply@example.com')


def _build_ics(booking, method='REQUEST'):
    """Build an iCalendar (.ics) string for a booking.

    method='REQUEST' creates/updates a calendar event.
    method='CANCEL' cancels an existing calendar event.
    Outlook, Google Calendar, and Apple Mail all honor these.
    """
    uid = booking.ensure_calendar_uid()
    organizer = current_app.config.get('MAIL_DEFAULT_SENDER', 'noreply@example.com')
    attendee = booking.user.email
    now = _ical_dt(booking.created_at or booking.start_time)
    dtstart = _ical_dt(booking.start_time)
    dtend = _ical_dt(booking.end_time)
    summary = f'{booking.title} - {booking.resource.name}'
    description = booking.notes or ''
    location = booking.resource.location or ''
    status = 'CANCELLED' if method == 'CANCEL' else 'CONFIRMED'
    sequence = 1 if method == 'CANCEL' else 0

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//ResourceManager//Booking//EN',
        f'METHOD:{method}',
        'BEGIN:VEVENT',
        f'UID:{uid}',
        f'DTSTAMP:{now}',
        f'DTSTART:{dtstart}',
        f'DTEND:{dtend}',
        f'SUMMARY:{_ical_escape(summary)}',
        f'DESCRIPTION:{_ical_escape(description)}',
        f'LOCATION:{_ical_escape(location)}',
        f'ORGANIZER:mailto:{organizer}',
        f'ATTENDEE;RSVP=TRUE:mailto:{attendee}',
        f'STATUS:{status}',
        f'SEQUENCE:{sequence}',
        'END:VEVENT',
        'END:VCALENDAR',
    ]
    return '\r\n'.join(lines)


def _ical_dt(dt):
    """Format a datetime as iCalendar UTC timestamp."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    return utc.strftime('%Y%m%dT%H%M%SZ')


def _ical_escape(text):
    """Escape special characters for iCalendar text fields."""
    return text.replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')


def _attach_ics(msg, ics_content, method='REQUEST'):
    """Attach an .ics file to the email as a calendar invite.

    Uses the text/calendar content type with METHOD parameter so that
    Outlook and other email clients treat it as a calendar invite
    (not just a file attachment).
    """
    # Add as an alternative part (inline calendar invite)
    part = MIMEBase('text', 'calendar', method=method, charset='UTF-8')
    part.set_payload(ics_content.encode('utf-8'))
    part.add_header('Content-Disposition', 'attachment', filename='invite.ics')
    msg.attach(part)


def send_booking_confirmation(booking):
    """Send booking confirmation email with .ics calendar invite."""
    if not _is_smtp_configured():
        logger.info('SMTP not configured, skipping confirmation email')
        return

    try:
        _update_mail_config()
        from app.extensions import db
        booking.ensure_calendar_uid()
        db.session.commit()

        msg = Message(
            subject=f'Booking Confirmed: {booking.title}',
            recipients=[booking.user.email],
        )
        msg.body = (
            f'Your booking has been confirmed.\n\n'
            f'Title: {booking.title}\n'
            f'Resource: {booking.resource.name}\n'
            f'Start: {booking.start_time.strftime("%Y-%m-%d %H:%M")}\n'
            f'End: {booking.end_time.strftime("%Y-%m-%d %H:%M")}\n'
            f'Notes: {booking.notes or "None"}\n\n'
            f'A calendar invite is attached — open it to add this booking to your calendar.\n\n'
            f'Thank you!'
        )
        msg.html = (
            f'<h2>Booking Confirmed</h2>'
            f'<p>Your booking has been confirmed.</p>'
            f'<table style="border-collapse: collapse;">'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">Title:</td><td>{booking.title}</td></tr>'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">Resource:</td><td>{booking.resource.name}</td></tr>'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">Start:</td><td>{booking.start_time.strftime("%Y-%m-%d %H:%M")}</td></tr>'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">End:</td><td>{booking.end_time.strftime("%Y-%m-%d %H:%M")}</td></tr>'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">Notes:</td><td>{booking.notes or "None"}</td></tr>'
            f'</table>'
            f'<p style="margin-top: 12px; color: #666;">A calendar invite (.ics) is attached to this email.</p>'
        )

        ics = _build_ics(booking, method='REQUEST')
        _attach_ics(msg, ics, method='REQUEST')

        mail.send(msg)
        logger.info(f'Confirmation email with calendar invite sent to {booking.user.email}')
    except Exception as e:
        logger.error(f'Failed to send confirmation email: {e}')


def send_booking_cancellation(booking):
    """Send booking cancellation email with .ics cancel event."""
    if not _is_smtp_configured():
        logger.info('SMTP not configured, skipping cancellation email')
        return

    try:
        _update_mail_config()
        from app.extensions import db
        booking.ensure_calendar_uid()
        db.session.commit()

        msg = Message(
            subject=f'Booking Cancelled: {booking.title}',
            recipients=[booking.user.email],
        )
        msg.body = (
            f'Your booking has been cancelled.\n\n'
            f'Title: {booking.title}\n'
            f'Resource: {booking.resource.name}\n'
            f'Start: {booking.start_time.strftime("%Y-%m-%d %H:%M")}\n'
            f'End: {booking.end_time.strftime("%Y-%m-%d %H:%M")}\n\n'
            f'The attached calendar update will remove this event from your calendar.'
        )
        msg.html = (
            f'<h2>Booking Cancelled</h2>'
            f'<p>Your booking has been cancelled.</p>'
            f'<table style="border-collapse: collapse;">'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">Title:</td><td>{booking.title}</td></tr>'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">Resource:</td><td>{booking.resource.name}</td></tr>'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">Start:</td><td>{booking.start_time.strftime("%Y-%m-%d %H:%M")}</td></tr>'
            f'<tr><td style="padding: 4px 12px; font-weight: bold;">End:</td><td>{booking.end_time.strftime("%Y-%m-%d %H:%M")}</td></tr>'
            f'</table>'
            f'<p style="margin-top: 12px; color: #666;">The attached calendar update (.ics) will remove this event from your calendar.</p>'
        )

        ics = _build_ics(booking, method='CANCEL')
        _attach_ics(msg, ics, method='CANCEL')

        mail.send(msg)
        logger.info(f'Cancellation email with calendar update sent to {booking.user.email}')
    except Exception as e:
        logger.error(f'Failed to send cancellation email: {e}')
