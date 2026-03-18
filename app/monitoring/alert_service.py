import json
import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)


def check_and_send_alerts(app, host_id, is_reachable):
    """Check if any alert rules should fire for a host that changed status."""
    with app.app_context():
        from app.extensions import db
        from app.models import AlertRule, MaintenanceWindow, ResourceHost, AppSettings

        host = db.session.get(ResourceHost, host_id)
        if not host or not host.critical:
            return

        resource = host.resource
        if not resource or not resource.is_active:
            return

        # Don't alert during maintenance windows
        if MaintenanceWindow.resource_in_maintenance(resource.id):
            return

        # Only alert when going offline (was reachable, now not)
        if is_reachable:
            return

        # Check previous ping to detect transition
        pings = list(host.ping_results.limit(2).all())
        if len(pings) < 2:
            return
        # pings[0] is latest (just recorded), pings[1] is previous
        if not pings[1].is_reachable:
            # Already was offline, don't re-alert
            return

        rules = AlertRule.query.filter_by(resource_id=resource.id, enabled=True).all()
        for rule in rules:
            try:
                if rule.alert_type == 'email':
                    _send_email_alert(app, rule, resource, host)
                elif rule.alert_type == 'webhook':
                    _send_webhook_alert(rule, resource, host)
                elif rule.alert_type == 'teams':
                    teams_url = AppSettings.get('teams_webhook_url', '')
                    if teams_url:
                        _send_teams_notification(teams_url, f"Host {host.address} on {resource.name} went OFFLINE")
                rule.last_triggered = datetime.now(timezone.utc)
                db.session.commit()
            except Exception as e:
                logger.error(f'Alert rule {rule.id} failed: {e}')


def _send_email_alert(app, rule, resource, host):
    """Send email alert for host going offline."""
    from flask_mail import Message
    from app.extensions import mail
    from app.email_service import _update_mail_config, _is_smtp_configured

    if not _is_smtp_configured():
        return

    with app.app_context():
        _update_mail_config()
        msg = Message(
            subject=f'ALERT: {resource.name} - {host.address} is OFFLINE',
            recipients=[rule.target],
        )
        msg.body = (
            f'Resource: {resource.name}\n'
            f'Host: {host.address} ({host.label})\n'
            f'Status: OFFLINE\n'
            f'Time: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}\n'
        )
        mail.send(msg)
        logger.info(f'Alert email sent to {rule.target} for {resource.name}')


def _send_webhook_alert(rule, resource, host):
    """Send webhook alert for host going offline."""
    payload = {
        'resource': resource.name,
        'host': host.address,
        'label': host.label,
        'status': 'offline',
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    resp = requests.post(rule.target, json=payload, timeout=10)
    resp.raise_for_status()
    logger.info(f'Webhook alert sent to {rule.target} for {resource.name}')


def _send_teams_notification(webhook_url, message):
    """Send a Microsoft Teams notification via incoming webhook."""
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.2",
                "body": [{
                    "type": "TextBlock",
                    "text": message,
                    "wrap": True,
                }],
            }
        }]
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f'Teams notification sent: {message[:50]}')
    except Exception as e:
        logger.error(f'Teams notification failed: {e}')


def send_teams_booking_notification(booking, action='created'):
    """Send a Teams notification for booking events."""
    from flask import current_app
    from app.models import AppSettings

    teams_url = AppSettings.get('teams_webhook_url', '')
    if not teams_url:
        return

    msg = (
        f"Booking **{action}**: {booking.title}\n\n"
        f"Resource: {booking.resource.name}\n"
        f"User: {booking.user.display_name}\n"
        f"Time: {booking.start_time.strftime('%Y-%m-%d %H:%M')} - {booking.end_time.strftime('%Y-%m-%d %H:%M')}"
    )
    _send_teams_notification(teams_url, msg)
