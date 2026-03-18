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
        from app.models import Resource, ResourceHost, PingResult

        hosts = (
            ResourceHost.query
            .join(Resource)
            .filter(Resource.is_active.is_(True))
            .all()
        )

        timeout = app.config.get('PING_TIMEOUT_SECONDS', 2)
        history_limit = app.config.get('PING_HISTORY_LIMIT', 100)

        for host in hosts:
            is_reachable, response_time, resolved_ip = ping_host(host.address, timeout)

            ping_result = PingResult(
                host_id=host.id,
                is_reachable=is_reachable,
                response_time_ms=response_time,
                resolved_ip=resolved_ip,
                checked_at=datetime.now(timezone.utc),
            )
            db.session.add(ping_result)

            # Prune old results
            count = PingResult.query.filter_by(host_id=host.id).count()
            if count > history_limit:
                old_results = (
                    PingResult.query
                    .filter_by(host_id=host.id)
                    .order_by(PingResult.checked_at.asc())
                    .limit(count - history_limit)
                    .all()
                )
                for old in old_results:
                    db.session.delete(old)

        db.session.commit()
        logger.info(f'Pinged {len(hosts)} hosts')
