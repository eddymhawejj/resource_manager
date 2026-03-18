import logging
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


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


def scan_subnets(subnet_id=None, max_workers=50, timeout=1, max_subnet_size=1024):
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

    snmp_community = AppSettings.get('snmp_community', 'public')

    new_hosts = []
    known_count = 0
    unreachable_count = 0
    total_scanned = 0
    skipped_subnets = []
    subnets_scanned = 0

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

        subnets_scanned += 1
        gateway_ip = subnet.gateway.strip() if subnet.gateway else None

        # Collect IPs to scan (exclude known + gateway)
        targets = []
        for ip in network.hosts():
            ip_str = str(ip)
            if ip_str == gateway_ip:
                continue
            if ip_str in known_addresses:
                known_count += 1
                continue
            targets.append(ip_str)

        total_scanned += len(targets)

        # Concurrent ping sweep
        responding_ips = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(ping_host, ip, timeout): ip for ip in targets}
            for future in as_completed(futures):
                ip_str = futures[future]
                try:
                    is_reachable, _, _ = future.result()
                    if is_reachable:
                        responding_ips.append(ip_str)
                    else:
                        unreachable_count += 1
                except Exception:
                    unreachable_count += 1

        # Resolve hostnames and create resources for new discoveries
        for ip_str in responding_ips:
            hostname = resolve_hostname(ip_str, snmp_community=snmp_community)
            name = hostname if hostname else ip_str

            # Double-check no resource was created since we started scanning
            existing = ResourceHost.query.filter_by(address=ip_str).first()
            if existing:
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
            known_addresses.add(ip_str)
            new_hosts.append(ip_str)

    AppSettings.set('subnet_last_scan', datetime.now(timezone.utc).isoformat())
    db.session.commit()

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
