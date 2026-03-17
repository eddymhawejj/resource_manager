import logging
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


def send_booking_confirmation(booking):
    """Send booking confirmation email."""
    if not _is_smtp_configured():
        logger.info('SMTP not configured, skipping confirmation email')
        return

    try:
        _update_mail_config()
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
        )
        mail.send(msg)
        logger.info(f'Confirmation email sent to {booking.user.email}')
    except Exception as e:
        logger.error(f'Failed to send confirmation email: {e}')


def send_booking_cancellation(booking):
    """Send booking cancellation email."""
    if not _is_smtp_configured():
        logger.info('SMTP not configured, skipping cancellation email')
        return

    try:
        _update_mail_config()
        msg = Message(
            subject=f'Booking Cancelled: {booking.title}',
            recipients=[booking.user.email],
        )
        msg.body = (
            f'Your booking has been cancelled.\n\n'
            f'Title: {booking.title}\n'
            f'Resource: {booking.resource.name}\n'
            f'Start: {booking.start_time.strftime("%Y-%m-%d %H:%M")}\n'
            f'End: {booking.end_time.strftime("%Y-%m-%d %H:%M")}\n'
        )
        mail.send(msg)
        logger.info(f'Cancellation email sent to {booking.user.email}')
    except Exception as e:
        logger.error(f'Failed to send cancellation email: {e}')
