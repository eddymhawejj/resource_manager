"""Switch VLAN sync service — pulls VLAN data from HP/Aruba switches via REST API.

Compatible with HP 2920-48G (j9728a) and other ArubaOS-Switch models that
support the REST API (firmware WB.16.x+).

REST API flow:
  1. POST /rest/v1/login-sessions  → get session cookie
  2. GET  /rest/v1/vlans           → get all VLANs
  3. GET  /rest/v1/vlans/{id}      → get VLAN detail (name, status)
  4. GET  /rest/v1/ipaddresses     → get IP interfaces (subnet/gateway info)
  5. DELETE /rest/v1/login-sessions → logout
"""
import logging
from datetime import datetime, timezone

import requests
import urllib3

from app.extensions import db
from app.models import AppSettings, Vlan, Subnet, ResourceHost

logger = logging.getLogger(__name__)

# Suppress InsecureRequestWarning when SSL verify is disabled
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _get_switch_config():
    """Read switch connection settings from AppSettings."""
    host = AppSettings.get('switch_host', '')
    username = AppSettings.get('switch_username', '')
    password = AppSettings.get('switch_password', '')
    use_ssl = AppSettings.get('switch_use_ssl', 'true') == 'true'
    verify_ssl = AppSettings.get('switch_verify_ssl', 'false') == 'true'
    return {
        'host': host.strip(),
        'username': username,
        'password': password,
        'use_ssl': use_ssl,
        'verify_ssl': verify_ssl,
    }


def is_switch_configured():
    """Check if switch connection is configured."""
    cfg = _get_switch_config()
    return bool(cfg['host'] and cfg['username'])


class SwitchAPIError(Exception):
    pass


class SwitchClient:
    """REST API client for ArubaOS-Switch (HP 2920 series)."""

    def __init__(self, host, username, password, use_ssl=True, verify_ssl=False):
        scheme = 'https' if use_ssl else 'http'
        self.base_url = f'{scheme}://{host}'
        self.username = username
        self.password = password
        self.verify = verify_ssl
        self.session = requests.Session()
        self.session.verify = self.verify
        self.cookie = None

    def login(self):
        """Authenticate and store session cookie."""
        url = f'{self.base_url}/rest/v1/login-sessions'
        try:
            r = self.session.post(url, json={
                'userName': self.username,
                'password': self.password,
            }, timeout=10)
        except requests.exceptions.ConnectionError as e:
            raise SwitchAPIError(f'Cannot connect to switch at {self.base_url}: {e}')
        except requests.exceptions.Timeout:
            raise SwitchAPIError(f'Connection to switch timed out')

        if r.status_code != 201 and r.status_code != 200:
            raise SwitchAPIError(f'Login failed (HTTP {r.status_code}): {r.text}')

        data = r.json()
        cookie_str = data.get('cookie', '')
        if '=' in cookie_str:
            self.cookie = cookie_str.split('=', 1)[1]
        else:
            self.cookie = cookie_str
        self.session.cookies.set('sessionId', self.cookie)

    def logout(self):
        """End the REST session."""
        if not self.cookie:
            return
        try:
            self.session.delete(
                f'{self.base_url}/rest/v1/login-sessions',
                timeout=5,
            )
        except Exception:
            pass

    def get_vlans(self):
        """Fetch all VLANs from the switch.

        Returns list of dicts: [{'vlan_id': int, 'name': str, 'status': str}, ...]
        """
        url = f'{self.base_url}/rest/v1/vlans'
        r = self.session.get(url, timeout=10)
        if r.status_code != 200:
            raise SwitchAPIError(f'Failed to fetch VLANs (HTTP {r.status_code})')

        data = r.json()
        vlans = []
        for entry in data.get('vlan_element', []):
            vlans.append({
                'vlan_id': entry.get('vlan_id'),
                'name': entry.get('name', ''),
                'status': entry.get('status', ''),
            })
        return vlans

    def get_ip_addresses(self):
        """Fetch IP address interfaces (to discover subnets/gateways).

        Returns list of dicts: [{'vlan_id': int, 'ip_address': {'octets': str, 'version': str},
                                  'ip_mask': {'octets': str}}, ...]
        """
        url = f'{self.base_url}/rest/v1/ipaddresses'
        try:
            r = self.session.get(url, timeout=10)
            if r.status_code != 200:
                logger.warning(f'Failed to fetch IP addresses (HTTP {r.status_code})')
                return []
            return r.json().get('ip_address_subnet_element', [])
        except Exception as e:
            logger.warning(f'Could not fetch IP addresses: {e}')
            return []


def sync_vlans_from_switch(app=None):
    """Connect to the switch, pull VLANs, and merge into the database.

    - New VLANs are created
    - Existing VLANs have their names updated
    - VLANs that exist in DB but not on switch are left alone (user may have added manually)
    - IP address interfaces are used to auto-create subnets

    Returns a summary dict.
    """
    from app.network.routes import _auto_link_hosts_to_subnet
    import ipaddress

    if app:
        ctx = app.app_context()
        ctx.push()

    try:
        cfg = _get_switch_config()
        if not cfg['host'] or not cfg['username']:
            logger.info('Switch not configured, skipping VLAN sync')
            return {'error': 'Switch not configured'}

        client = SwitchClient(
            host=cfg['host'],
            username=cfg['username'],
            password=cfg['password'],
            use_ssl=cfg['use_ssl'],
            verify_ssl=cfg['verify_ssl'],
        )

        client.login()

        try:
            switch_vlans = client.get_vlans()
            ip_addresses = client.get_ip_addresses()
        finally:
            client.logout()

        created = 0
        updated = 0
        subnets_created = 0

        for sv in switch_vlans:
            vlan_num = sv['vlan_id']
            vlan_name = sv['name'] or f'VLAN {vlan_num}'

            existing = Vlan.query.filter_by(number=vlan_num).first()
            if existing:
                if existing.name != vlan_name:
                    existing.name = vlan_name
                    updated += 1
            else:
                vlan = Vlan(number=vlan_num, name=vlan_name)
                db.session.add(vlan)
                created += 1

        db.session.flush()

        # Process IP address interfaces to create subnets
        for iface in ip_addresses:
            try:
                vlan_id_num = iface.get('vlan_id')
                ip_info = iface.get('ip_address', {})
                mask_info = iface.get('ip_mask', {})

                ip_str = ip_info.get('octets', '')
                mask_str = mask_info.get('octets', '')

                if not ip_str or not mask_str:
                    continue

                # Convert IP + mask to CIDR
                network = ipaddress.IPv4Network(f'{ip_str}/{mask_str}', strict=False)
                cidr = str(network)

                vlan = Vlan.query.filter_by(number=vlan_id_num).first()
                if not vlan:
                    continue

                existing_subnet = Subnet.query.filter_by(cidr=cidr).first()
                if not existing_subnet:
                    subnet = Subnet(
                        vlan_id=vlan.id,
                        cidr=cidr,
                        gateway=ip_str,
                        name=vlan.name,
                    )
                    db.session.add(subnet)
                    db.session.flush()
                    _auto_link_hosts_to_subnet(subnet)
                    subnets_created += 1
                else:
                    # Update gateway if changed
                    if existing_subnet.gateway != ip_str:
                        existing_subnet.gateway = ip_str
            except Exception as e:
                logger.warning(f'Error processing IP interface: {e}')
                continue

        AppSettings.set('switch_last_sync', datetime.now(timezone.utc).isoformat())
        db.session.commit()

        summary = {
            'vlans_created': created,
            'vlans_updated': updated,
            'subnets_created': subnets_created,
            'total_switch_vlans': len(switch_vlans),
        }
        logger.info(f'Switch sync complete: {summary}')
        return summary

    except SwitchAPIError as e:
        logger.error(f'Switch sync failed: {e}')
        return {'error': str(e)}
    except Exception as e:
        logger.error(f'Switch sync failed unexpectedly: {e}')
        return {'error': str(e)}
    finally:
        if app:
            ctx.pop()
