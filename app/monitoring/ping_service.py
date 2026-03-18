import subprocess
import re
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def ping_host(host, timeout=2):
    """Ping a host (IP or hostname) and return (is_reachable, response_time_ms, resolved_ip)."""
    if not host:
        return False, None, None

    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', str(timeout), host],
            capture_output=True, text=True, timeout=timeout + 2,
        )

        resolved_ip = None
        # Extract resolved IP from ping output, e.g. "PING host (1.2.3.4)"
        ip_match = re.search(r'PING\s+\S+\s+\((\d+\.\d+\.\d+\.\d+)\)', result.stdout)
        if ip_match:
            resolved_ip = ip_match.group(1)

        if result.returncode == 0:
            match = re.search(r'time[=<]([\d.]+)\s*ms', result.stdout)
            response_time = float(match.group(1)) if match else None
            return True, response_time, resolved_ip
        return False, None, resolved_ip

    except (subprocess.TimeoutExpired, OSError, Exception) as e:
        logger.debug(f'Ping failed for {host}: {e}')
        return False, None, None


def ping_all_resources(app):
    """Background job: ping all hosts of active resources."""
    with app.app_context():
        from app.extensions import db
        from app.models import Resource, ResourceHost, PingResult, MaintenanceWindow

        hosts = (
            ResourceHost.query
            .join(Resource)
            .filter(Resource.is_active.is_(True))
            .filter(Resource.resource_type != 'device')
            .all()
        )

        timeout = app.config.get('PING_TIMEOUT_SECONDS', 2)
        history_limit = app.config.get('PING_HISTORY_LIMIT', 100)

        # Collect host IDs upfront, then ping outside the query context
        # to avoid holding a DB transaction open during slow ICMP pings
        host_info = [(h.id, h.address, h.resource_id) for h in hosts]

        for host_id, address, resource_id in host_info:
            # Skip hosts whose resource is in maintenance
            if MaintenanceWindow.resource_in_maintenance(resource_id):
                continue

            is_reachable, response_time, resolved_ip = ping_host(address, timeout)

            ping_result = PingResult(
                host_id=host_id,
                is_reachable=is_reachable,
                response_time_ms=response_time,
                resolved_ip=resolved_ip,
                checked_at=datetime.now(timezone.utc),
            )
            db.session.add(ping_result)

            # Prune old results
            count = PingResult.query.filter_by(host_id=host_id).count()
            if count > history_limit:
                old_results = (
                    PingResult.query
                    .filter_by(host_id=host_id)
                    .order_by(PingResult.checked_at.asc())
                    .limit(count - history_limit)
                    .all()
                )
                for old in old_results:
                    db.session.delete(old)

            # Commit per host to minimize lock duration
            db.session.commit()

            # Check alert rules
            try:
                from app.monitoring.alert_service import check_and_send_alerts
                check_and_send_alerts(app, host_id, is_reachable)
            except Exception as e:
                logger.error(f'Alert check failed for host {host_id}: {e}')

        logger.info(f'Pinged {len(host_info)} hosts')
