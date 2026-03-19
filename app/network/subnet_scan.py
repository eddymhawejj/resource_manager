import logging
import re
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# In-memory scan progress, read by the progress endpoint
_scan_progress = {
    'running': False,
    'phase': '',          # 'pinging', 'resolving', 'done', 'error'
    'subnet': '',         # current subnet CIDR
    'scanned': 0,         # IPs pinged so far
    'total': 0,           # total IPs to ping
    'found': 0,           # responding IPs found
    'new_hosts': 0,       # resources created
    'subnets_done': 0,
    'subnets_total': 0,
    'result': None,       # final summary dict when done
}
_scan_lock = threading.Lock()


def get_scan_progress():
    """Return a snapshot of current scan progress."""
    with _scan_lock:
        return dict(_scan_progress)


def _update_progress(**kwargs):
    with _scan_lock:
        _scan_progress.update(kwargs)


def resolve_hostname(ip, snmp_community='public', timeout=2):
    """Try to resolve a hostname for an IP using multiple methods.

    Tries in order: reverse DNS, SNMP sysName, NetBIOS.
    Returns the first successful result, or None.
    """
    # 1. Reverse DNS
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        if hostname and hostname != ip:
            return hostname
    except (socket.herror, socket.gaierror, OSError):
        pass

    # 2. SNMP sysName (OID 1.3.6.1.2.1.1.5.0)
    try:
        from pysnmp.hlapi import (
            CommunityData, ContextData, ObjectIdentity, ObjectType,
            SnmpEngine, UdpTransportTarget, getCmd,
        )
        engine = SnmpEngine()
        error_indication, error_status, _, var_binds = next(
            getCmd(
                engine,
                CommunityData(snmp_community),
                UdpTransportTarget((ip, 161), timeout=timeout, retries=0),
                ContextData(),
                ObjectType(ObjectIdentity('1.3.6.1.2.1.1.5.0')),
            )
        )
        if not error_indication and not error_status and var_binds:
            value = str(var_binds[0][1]).strip()
            if value and value != ip:
                return value
    except ImportError:
        logger.debug('pysnmp not installed, skipping SNMP resolution')
    except Exception as e:
        logger.debug(f'SNMP sysName query failed for {ip}: {e}')

    # 3. NetBIOS via nmblookup
    try:
        result = subprocess.run(
            ['nmblookup', '-A', ip],
            capture_output=True, text=True, timeout=timeout + 1,
        )
        if result.returncode == 0:
            # Look for <00> entry (workstation/server name)
            match = re.search(r'^\s+(\S+)\s+<00>', result.stdout, re.MULTILINE)
            if match:
                name = match.group(1).strip()
                if name and name != ip:
                    return name
    except FileNotFoundError:
        logger.debug('nmblookup not installed, skipping NetBIOS resolution')
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug(f'NetBIOS lookup failed for {ip}: {e}')

    return None


def is_scan_running():
    with _scan_lock:
        return _scan_progress['running']


def start_scan_background(app, subnet_id=None, max_workers=50, timeout=1, max_subnet_size=65534):
    """Launch a subnet scan in a background thread. Returns False if already running."""
    if is_scan_running():
        return False

    _update_progress(
        running=True, phase='starting', subnet='', scanned=0, total=0,
        found=0, new_hosts=0, subnets_done=0, subnets_total=0, result=None,
    )

    thread = threading.Thread(
        target=_run_scan,
        args=(app, subnet_id, max_workers, timeout, max_subnet_size),
        daemon=True,
    )
    thread.start()
    return True


def _run_scan(app, subnet_id, max_workers, timeout, max_subnet_size):
    """Background scan thread entry point."""
    try:
        with app.app_context():
            result = scan_subnets(subnet_id, max_workers, timeout, max_subnet_size)
            _update_progress(phase='done', running=False, result=result)
    except Exception as e:
        logger.error(f'Background subnet scan failed: {e}')
        _update_progress(phase='error', running=False, result={'error': str(e)})


def scan_subnets(subnet_id=None, max_workers=50, timeout=1, max_subnet_size=65534):
    """Ping-sweep subnets to discover active hosts not already tracked.

    Args:
        subnet_id: Specific subnet ID to scan, or None for all subnets.
        max_workers: ThreadPoolExecutor concurrency limit.
        timeout: Ping timeout in seconds.
        max_subnet_size: Max host IPs per subnet before skipping.

    Returns:
        dict with new_hosts, known_hosts, unreachable, skipped_subnets,
        total_scanned, subnets_scanned.
    """
    from app.extensions import db
    from app.models import AppSettings, Resource, ResourceHost, Subnet
    from app.monitoring.ping_service import ping_host

    # Gather target subnets
    if subnet_id:
        subnet = db.session.get(Subnet, subnet_id)
        if not subnet:
            return {'error': f'Subnet {subnet_id} not found'}
        subnets = [subnet]
    else:
        subnets = Subnet.query.all()

    if not subnets:
        return {'error': 'No subnets configured'}

    # Build set of already-known host addresses
    known_addresses = {h.address for h in ResourceHost.query.with_entities(ResourceHost.address).all()}

    # Collect existing scan-discovered hosts missing a hostname host entry,
    # so we can backfill on rescan.
    hosts_needing_hostname = []
    # Resource IDs that already have a hostname-based host entry
    resources_with_hostname_host = {
        rh.resource_id for rh in ResourceHost.query.filter_by(label='Hostname').all()
    }
    for rh in ResourceHost.query.filter_by(label='Subnet scan').all():
        if rh.resource_id not in resources_with_hostname_host:
            hosts_needing_hostname.append(rh)

    snmp_community = AppSettings.get('snmp_community', 'public')

    new_hosts = []
    known_count = 0
    unreachable_count = 0
    total_scanned = 0
    skipped_subnets = []
    subnets_scanned = 0

    # Pre-calculate total IPs across all subnets for progress tracking
    all_targets = []  # list of (subnet, targets_list)
    for subnet in subnets:
        try:
            network = subnet.network
        except (ValueError, TypeError) as e:
            logger.warning(f'Invalid subnet CIDR {subnet.cidr}: {e}')
            skipped_subnets.append(f'{subnet.cidr} (invalid)')
            continue

        host_count = network.num_addresses - 2
        if host_count <= 0:
            continue
        if host_count > max_subnet_size:
            skipped_subnets.append(f'{subnet.cidr} ({host_count} hosts)')
            continue

        gateway_ip = subnet.gateway.strip() if subnet.gateway else None
        targets = []
        for ip in network.hosts():
            ip_str = str(ip)
            if ip_str == gateway_ip:
                continue
            if ip_str in known_addresses:
                known_count += 1
                continue
            targets.append(ip_str)

        if targets:
            all_targets.append((subnet, targets))
            total_scanned += len(targets)

    total_subnets = len(all_targets)
    _update_progress(
        phase='pinging', total=total_scanned, scanned=0,
        subnets_total=total_subnets, subnets_done=0,
    )

    scanned_so_far = 0
    found_so_far = 0

    for subnet, targets in all_targets:
        subnets_scanned += 1
        _update_progress(subnet=subnet.cidr)

        # Concurrent ping sweep
        responding_ips = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(ping_host, ip, timeout): ip for ip in targets}
            for future in as_completed(futures):
                ip_str = futures[future]
                scanned_so_far += 1
                try:
                    is_reachable, _, _ = future.result()
                    if is_reachable:
                        responding_ips.append(ip_str)
                        found_so_far += 1
                    else:
                        unreachable_count += 1
                except Exception:
                    unreachable_count += 1

                # Update progress every 10 IPs to avoid excessive lock contention
                if scanned_so_far % 10 == 0 or scanned_so_far == total_scanned:
                    _update_progress(scanned=scanned_so_far, found=found_so_far)

        _update_progress(
            phase='resolving', subnet=subnet.cidr,
            scanned=scanned_so_far, found=found_so_far,
        )

        # Resolve hostnames and create resources for new discoveries
        for ip_str in responding_ips:
            hostname = resolve_hostname(ip_str, snmp_community=snmp_community)
            name = hostname if hostname else ip_str

            # Double-check no resource was created since we started scanning
            existing = ResourceHost.query.filter_by(address=ip_str).first()
            if existing:
                known_count += 1
                continue

            # If hostname resolved, check if a resource with that name already
            # exists (e.g. same device got a new DHCP IP). Update it instead of
            # creating a duplicate.
            if hostname:
                existing_resource = Resource.query.filter_by(name=name).first()
                if existing_resource:
                    # Update the existing host entry's IP, or add a new host
                    existing_host = existing_resource.hosts.filter_by(
                        label='Subnet scan'
                    ).first()
                    if existing_host:
                        existing_host.address = ip_str
                        existing_host.subnet_id = subnet.id
                    else:
                        host = ResourceHost(
                            resource_id=existing_resource.id,
                            address=ip_str,
                            label='Subnet scan',
                            critical=True,
                            subnet_id=subnet.id,
                        )
                        db.session.add(host)

                    # Update the hostname host entry if one exists
                    existing_hostname_host = existing_resource.hosts.filter_by(
                        label='Hostname'
                    ).first()
                    if existing_hostname_host:
                        existing_hostname_host.address = hostname
                    else:
                        hostname_host = ResourceHost(
                            resource_id=existing_resource.id,
                            address=hostname,
                            label='Hostname',
                            critical=True,
                            subnet_id=subnet.id,
                        )
                        db.session.add(hostname_host)

                    known_addresses.add(ip_str)
                    known_count += 1
                    continue

            resource = Resource(
                name=name,
                description=f'Discovered via subnet scan of {subnet.cidr}',
                resource_type='device',
                is_active=True,
            )
            db.session.add(resource)
            db.session.flush()

            host = ResourceHost(
                resource_id=resource.id,
                address=ip_str,
                label='Subnet scan',
                critical=True,
                subnet_id=subnet.id,
            )
            db.session.add(host)

            # If we resolved a hostname, add it as a second host entry so
            # monitoring uses the hostname (stable) instead of the IP (DHCP).
            if hostname:
                hostname_host = ResourceHost(
                    resource_id=resource.id,
                    address=hostname,
                    label='Hostname',
                    critical=True,
                    subnet_id=subnet.id,
                )
                db.session.add(hostname_host)

            known_addresses.add(ip_str)
            new_hosts.append(ip_str)

        db.session.commit()
        _update_progress(
            phase='pinging', subnets_done=subnets_scanned,
            new_hosts=len(new_hosts),
        )

    # Backfill hostname host entries for previously discovered hosts that
    # are missing one (e.g. created before hostname-host logic was added).
    if hosts_needing_hostname:
        _update_progress(phase='resolving', subnet='backfill')
        backfilled = 0
        for rh in hosts_needing_hostname:
            hostname = resolve_hostname(rh.address, snmp_community=snmp_community)
            if hostname:
                hostname_host = ResourceHost(
                    resource_id=rh.resource_id,
                    address=hostname,
                    label='Hostname',
                    critical=True,
                    subnet_id=rh.subnet_id,
                )
                db.session.add(hostname_host)
                backfilled += 1
        if backfilled:
            db.session.commit()
        logger.info(f'Backfilled {backfilled} hostname hosts for {len(hosts_needing_hostname)} existing hosts')

    AppSettings.set('subnet_last_scan', datetime.now(timezone.utc).isoformat())

    summary = {
        'new_hosts': new_hosts,
        'known_hosts': known_count,
        'unreachable': unreachable_count,
        'skipped_subnets': skipped_subnets,
        'total_scanned': total_scanned,
        'subnets_scanned': subnets_scanned,
    }
    logger.info(f'Subnet scan complete: {len(new_hosts)} new, {known_count} known, '
                f'{unreachable_count} unreachable across {subnets_scanned} subnets')
    return summary
