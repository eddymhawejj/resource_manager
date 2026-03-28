"""Performance tests for key pages.

Ensures pages load within acceptable time limits for 30-50 concurrent users.
Single-request tests use tight budgets; concurrent tests simulate parallel load.
"""

import time
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from tests.conftest import login
from app.models import (
    Vlan, Subnet, Resource, ResourceHost, AccessPoint, ResourceGroup,
    Booking, User,
)
from datetime import datetime, timedelta, timezone


def _create_subnet_with_hosts(db, vlan, cidr, host_count, resource):
    """Create a subnet and populate it with hosts."""
    subnet = Subnet(vlan_id=vlan.id, cidr=cidr, name=f'Sub {cidr}')
    db.session.add(subnet)
    db.session.flush()
    base_ip = cidr.rsplit('.', 1)[0]  # e.g. '10.0.1'
    for i in range(1, host_count + 1):
        host = ResourceHost(
            resource_id=resource.id,
            address=f'{base_ip}.{i}',
            label=f'host-{i}',
            critical=True,
            subnet_id=subnet.id,
        )
        db.session.add(host)
    return subnet


class TestIPAMPerformance:
    """IPAM page should stay fast even with many subnets and hosts."""

    def test_ipam_no_dns_lookups(self, client, admin_user, db):
        """Verify IPAM page does not perform DNS resolution for hostname hosts."""
        vlan = Vlan(number=10, name='Perf VLAN')
        db.session.add(vlan)
        db.session.flush()
        resource = Resource(name='PerfRes', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()

        subnet = Subnet(vlan_id=vlan.id, cidr='10.50.0.0/24', name='DNS Test')
        db.session.add(subnet)
        db.session.flush()

        # Add hosts with hostnames (not IPs) — these would trigger DNS if buggy
        for i in range(5):
            db.session.add(ResourceHost(
                resource_id=resource.id,
                address=f'server-{i}.lab.local',
                label=f'srv-{i}',
                critical=True,
                subnet_id=subnet.id,
            ))
        db.session.commit()

        login(client, 'admin', 'adminpass')
        with patch('socket.gethostbyname') as mock_dns:
            resp = client.get('/network/ipam')
            assert resp.status_code == 200
            mock_dns.assert_not_called()

    def test_ipam_many_subnets(self, client, admin_user, db):
        """IPAM with 50 subnets x 20 hosts each loads under 2 seconds."""
        vlan = Vlan(number=20, name='Big VLAN')
        db.session.add(vlan)
        db.session.flush()
        resource = Resource(name='BigRes', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()

        for subnet_idx in range(50):
            _create_subnet_with_hosts(
                db, vlan,
                f'10.{subnet_idx}.0.0/24',
                20, resource,
            )
        db.session.commit()

        login(client, 'admin', 'adminpass')
        start = time.monotonic()
        resp = client.get('/network/ipam')
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 2.0, f'IPAM took {elapsed:.2f}s with 50 subnets x 20 hosts'

    def test_ipam_hostname_hosts_no_slowdown(self, client, admin_user, db):
        """IPAM with hostname-based hosts should not be slower than IP-based."""
        vlan = Vlan(number=30, name='Hostname VLAN')
        db.session.add(vlan)
        db.session.flush()
        resource = Resource(name='HostnameRes', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()

        subnet = Subnet(vlan_id=vlan.id, cidr='172.16.0.0/22', name='Big Subnet')
        db.session.add(subnet)
        db.session.flush()

        # 50 hostname-based hosts — would have caused 50 DNS lookups before the fix
        for i in range(50):
            db.session.add(ResourceHost(
                resource_id=resource.id,
                address=f'node-{i}.cluster.lab.local',
                label=f'node-{i}',
                critical=True,
                subnet_id=subnet.id,
            ))
        db.session.commit()

        login(client, 'admin', 'adminpass')
        start = time.monotonic()
        resp = client.get('/network/ipam')
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 1.0, f'IPAM with 50 hostname hosts took {elapsed:.2f}s (DNS leak?)'


class TestResourceDetailPerformance:
    """Resource detail page performance with many access points and hosts."""

    def test_detail_many_access_points(self, client, admin_user, db):
        """Detail page with 30 access points loads under 1 second."""
        resource = Resource(name='AP Heavy', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()
        for i in range(30):
            ap = AccessPoint(
                resource_id=resource.id,
                protocol='rdp' if i % 2 == 0 else 'ssh',
                hostname=f'10.0.0.{i + 1}',
                display_name=f'AP #{i + 1}',
            )
            db.session.add(ap)
        db.session.commit()

        login(client, 'admin', 'adminpass')
        start = time.monotonic()
        resp = client.get(f'/resources/{resource.id}')
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 1.0, f'Detail with 30 APs took {elapsed:.2f}s'

    def test_detail_many_hosts(self, client, admin_user, db):
        """Detail page with 50 hosts loads under 1 second."""
        resource = Resource(name='Host Heavy', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()
        for i in range(50):
            db.session.add(ResourceHost(
                resource_id=resource.id,
                address=f'10.1.0.{i + 1}',
                label=f'Host {i + 1}',
                critical=(i % 3 == 0),
            ))
        db.session.commit()

        login(client, 'admin', 'adminpass')
        start = time.monotonic()
        resp = client.get(f'/resources/{resource.id}')
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 1.0, f'Detail with 50 hosts took {elapsed:.2f}s'

    def test_detail_group_filtered_access_points(self, client, regular_user, db):
        """Verify group-restricted APs are filtered without performance penalty."""
        resource = Resource(name='Group AP Test', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()

        group = ResourceGroup(name='Admins Only')
        db.session.add(group)
        db.session.flush()

        # 10 unrestricted + 10 group-restricted
        for i in range(10):
            db.session.add(AccessPoint(
                resource_id=resource.id, protocol='rdp',
                hostname=f'10.2.0.{i + 1}', display_name=f'Public #{i + 1}',
            ))
        for i in range(10):
            db.session.add(AccessPoint(
                resource_id=resource.id, protocol='rdp',
                hostname=f'10.2.1.{i + 1}', display_name=f'Restricted #{i + 1}',
                required_group_id=group.id,
            ))
        db.session.commit()

        login(client, 'testuser', 'userpass')
        start = time.monotonic()
        resp = client.get(f'/resources/{resource.id}')
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 1.0, f'Detail with group filtering took {elapsed:.2f}s'
        # User not in group — should only see the 10 public APs
        assert b'Public #1' in resp.data
        assert b'Restricted #1' not in resp.data


class TestResourceListPerformance:
    """Resource listing page with many resources and access points."""

    def test_list_many_resources(self, client, admin_user, db):
        """Listing page with 100 testbeds loads under 2 seconds."""
        for i in range(100):
            r = Resource(name=f'Testbed-{i:03d}', resource_type='testbed', is_active=True)
            db.session.add(r)
            db.session.flush()
            # 2 hosts and 2 APs per resource
            for j in range(2):
                db.session.add(ResourceHost(
                    resource_id=r.id,
                    address=f'10.{i % 256}.{j}.1',
                    label=f'h{j}',
                    critical=True,
                ))
                db.session.add(AccessPoint(
                    resource_id=r.id, protocol='rdp',
                    hostname=f'10.{i % 256}.{j}.1',
                ))
        db.session.commit()

        login(client, 'admin', 'adminpass')
        start = time.monotonic()
        resp = client.get('/resources/')
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 2.0, f'Resource list with 100 testbeds took {elapsed:.2f}s'


class TestNetworkOverviewPerformance:
    """Network overview page with many VLANs, subnets, and hosts."""

    def test_overview_many_vlans(self, client, admin_user, db):
        """Network overview with 20 VLANs x 5 subnets x 10 hosts loads under 2s."""
        resource = Resource(name='NetRes', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()

        for v in range(20):
            vlan = Vlan(number=100 + v, name=f'VLAN-{100 + v}')
            db.session.add(vlan)
            db.session.flush()
            for s in range(5):
                _create_subnet_with_hosts(
                    db, vlan,
                    f'10.{v}.{s}.0/24',
                    10, resource,
                )
        db.session.commit()

        login(client, 'admin', 'adminpass')
        start = time.monotonic()
        resp = client.get('/network/')
        elapsed = time.monotonic() - start

        assert resp.status_code == 200
        assert elapsed < 2.0, f'Network overview took {elapsed:.2f}s with 20 VLANs'


class TestCalendarPerformance:
    """Calendar/bookings page with many bookings."""

    def test_calendar_many_bookings(self, client, admin_user, db):
        """Calendar API with 200 bookings responds under 1 second."""
        resource = Resource(name='CalRes', resource_type='testbed', is_active=True)
        db.session.add(resource)
        db.session.flush()

        now = datetime.now(timezone.utc)
        for i in range(200):
            db.session.add(Booking(
                resource_id=resource.id,
                user_id=admin_user.id,
                title=f'Booking {i}',
                start_time=now + timedelta(hours=i),
                end_time=now + timedelta(hours=i + 1),
                status='confirmed',
            ))
        db.session.commit()

        login(client, 'admin', 'adminpass')

        # Calendar page load
        start = time.monotonic()
        resp = client.get('/bookings/calendar')
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert elapsed < 1.0, f'Calendar page took {elapsed:.2f}s with 200 bookings'


class TestConcurrentLoad:
    """Simulate 30-50 concurrent users hitting key pages."""

    def _setup_realistic_data(self, db):
        """Create a realistic dataset: resources, hosts, subnets, bookings."""
        vlan = Vlan(number=50, name='Concurrent VLAN')
        db.session.add(vlan)
        db.session.flush()

        resources = []
        for i in range(20):
            r = Resource(name=f'ConcRes-{i}', resource_type='testbed', is_active=True)
            db.session.add(r)
            db.session.flush()
            resources.append(r)

            subnet = Subnet(vlan_id=vlan.id, cidr=f'10.{50 + i}.0.0/24', name=f'Sub-{i}')
            db.session.add(subnet)
            db.session.flush()

            for j in range(5):
                db.session.add(ResourceHost(
                    resource_id=r.id, address=f'10.{50 + i}.0.{j + 1}',
                    label=f'h{j}', critical=True, subnet_id=subnet.id,
                ))
            for j in range(2):
                db.session.add(AccessPoint(
                    resource_id=r.id, protocol='rdp',
                    hostname=f'10.{50 + i}.0.{j + 1}',
                    display_name=f'AP-{j}',
                ))
        db.session.commit()
        return resources

    def _run_concurrent(self, app, n_clients, url, setup_fn=None):
        """Fire n_clients requests concurrently, return (times, statuses)."""
        results = []

        def make_request(_):
            with app.test_client() as c:
                # Login
                c.post('/auth/login', data={
                    'username': 'admin', 'password': 'adminpass',
                    'auth_type': 'local',
                }, follow_redirects=True)
                if setup_fn:
                    setup_fn(c)
                start = time.monotonic()
                resp = c.get(url)
                elapsed = time.monotonic() - start
                return elapsed, resp.status_code

        with ThreadPoolExecutor(max_workers=n_clients) as pool:
            futures = [pool.submit(make_request, i) for i in range(n_clients)]
            for f in as_completed(futures):
                results.append(f.result())

        times = [r[0] for r in results]
        statuses = [r[1] for r in results]
        return times, statuses

    def test_concurrent_resource_list_50_users(self, app, admin_user, db):
        """50 concurrent users loading the resource list page."""
        self._setup_realistic_data(db)
        times, statuses = self._run_concurrent(app, 50, '/resources/')

        assert all(s == 200 for s in statuses), f'Some requests failed: {statuses}'
        avg_time = sum(times) / len(times)
        max_time = max(times)
        assert avg_time < 2.0, f'Avg response {avg_time:.2f}s for 50 concurrent users'
        assert max_time < 5.0, f'Max response {max_time:.2f}s (slowest user)'

    def test_concurrent_resource_detail_30_users(self, app, admin_user, db):
        """30 concurrent users loading the same resource detail page."""
        resources = self._setup_realistic_data(db)
        rid = resources[0].id
        times, statuses = self._run_concurrent(app, 30, f'/resources/{rid}')

        assert all(s == 200 for s in statuses)
        avg_time = sum(times) / len(times)
        max_time = max(times)
        assert avg_time < 1.5, f'Avg response {avg_time:.2f}s for 30 concurrent users'
        assert max_time < 4.0, f'Max response {max_time:.2f}s (slowest user)'

    def test_concurrent_ipam_30_users(self, app, admin_user, db):
        """30 concurrent users loading the IPAM page."""
        self._setup_realistic_data(db)
        times, statuses = self._run_concurrent(app, 30, '/network/ipam')

        assert all(s == 200 for s in statuses)
        avg_time = sum(times) / len(times)
        max_time = max(times)
        assert avg_time < 2.0, f'Avg response {avg_time:.2f}s for 30 concurrent users'
        assert max_time < 5.0, f'Max response {max_time:.2f}s (slowest user)'

    def test_concurrent_network_overview_30_users(self, app, admin_user, db):
        """30 concurrent users loading the network overview."""
        self._setup_realistic_data(db)
        times, statuses = self._run_concurrent(app, 30, '/network/')

        assert all(s == 200 for s in statuses)
        avg_time = sum(times) / len(times)
        max_time = max(times)
        assert avg_time < 2.0, f'Avg response {avg_time:.2f}s for 30 concurrent users'
        assert max_time < 5.0, f'Max response {max_time:.2f}s (slowest user)'

    def test_concurrent_mixed_pages_50_users(self, app, admin_user, db):
        """50 users hitting different pages simultaneously."""
        resources = self._setup_realistic_data(db)
        rid = resources[0].id
        urls = [
            '/resources/',
            f'/resources/{rid}',
            '/network/',
            '/network/ipam',
            '/bookings/calendar',
        ]
        results = []

        def make_request(i):
            url = urls[i % len(urls)]
            with app.test_client() as c:
                c.post('/auth/login', data={
                    'username': 'admin', 'password': 'adminpass',
                    'auth_type': 'local',
                }, follow_redirects=True)
                start = time.monotonic()
                resp = c.get(url)
                elapsed = time.monotonic() - start
                return elapsed, resp.status_code, url

        with ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(make_request, i) for i in range(50)]
            for f in as_completed(futures):
                results.append(f.result())

        times = [r[0] for r in results]
        statuses = [r[1] for r in results]
        avg_time = sum(times) / len(times)
        max_time = max(times)

        assert all(s == 200 for s in statuses), f'Failed requests: {[(r[2], r[1]) for r in results if r[1] != 200]}'
        assert avg_time < 2.0, f'Avg response {avg_time:.2f}s across 50 concurrent mixed requests'
        assert max_time < 5.0, f'Max response {max_time:.2f}s (slowest request)'
