"""Tests for network auto-linking and subnet scanning."""

from unittest.mock import patch

from app.models import (
    Resource, ResourceHost, Vlan, Subnet,
    find_subnet_for_address, _resolve_to_ip,
)


class TestResolveToIp:
    def test_resolve_ipv4(self):
        import ipaddress
        result = _resolve_to_ip('192.168.1.1')
        assert result == ipaddress.ip_address('192.168.1.1')

    def test_resolve_invalid(self):
        result = _resolve_to_ip('not-a-valid-host-xyzzy.invalid')
        assert result is None

    @patch('socket.gethostbyname', return_value='10.0.0.5')
    def test_resolve_hostname(self, mock_dns):
        import ipaddress
        result = _resolve_to_ip('myserver.local')
        assert result == ipaddress.ip_address('10.0.0.5')
        mock_dns.assert_called_once_with('myserver.local')


class TestFindSubnetForAddress:
    def test_find_matching_subnet(self, db):
        vlan = Vlan(number=500, name='V500')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='10.10.0.0/24', name='Match')
        db.session.add(subnet)
        db.session.commit()
        result = find_subnet_for_address('10.10.0.50')
        assert result is not None
        assert result.id == subnet.id

    def test_no_matching_subnet(self, db):
        vlan = Vlan(number=501, name='V501')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='10.20.0.0/24', name='Other')
        db.session.add(subnet)
        db.session.commit()
        result = find_subnet_for_address('172.16.0.1')
        assert result is None

    def test_invalid_address(self, db):
        result = find_subnet_for_address('not-an-ip-at-all')
        assert result is None


class TestAutoLinkSubnet:
    def test_host_auto_links_to_subnet(self, db, sample_resource):
        vlan = Vlan(number=600, name='V600')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='192.168.1.0/24', name='Auto')
        db.session.add(subnet)
        db.session.commit()

        host = ResourceHost(
            resource_id=sample_resource.id, address='192.168.1.10',
            label='AutoLink', critical=True,
        )
        host.auto_link_subnet()
        db.session.add(host)
        db.session.commit()
        assert host.subnet_id == subnet.id

    def test_host_no_matching_subnet(self, db, sample_resource):
        host = ResourceHost(
            resource_id=sample_resource.id, address='99.99.99.99',
            label='NoSubnet', critical=True,
        )
        host.auto_link_subnet()
        assert host.subnet_id is None


class TestNetworkRelink:
    def test_relink_all_hosts(self, client, admin_user, db, sample_resource):
        from tests.conftest import login
        vlan = Vlan(number=700, name='V700')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='10.50.0.0/24', name='Relink')
        db.session.add(subnet)
        db.session.commit()

        host = ResourceHost(
            resource_id=sample_resource.id, address='10.50.0.5',
            label='Unlinked', critical=True,
        )
        db.session.add(host)
        db.session.commit()
        assert host.subnet_id is None

        login(client, 'admin', 'adminpass')
        resp = client.post('/network/relink', follow_redirects=True)
        assert resp.status_code == 200
        db.session.refresh(host)
        assert host.subnet_id == subnet.id


class TestNetworkIPAM:
    def test_ipam_page(self, client, regular_user, db):
        from tests.conftest import login
        login(client, 'testuser', 'userpass')
        resp = client.get('/network/ipam')
        assert resp.status_code == 200


class TestNetworkTopology:
    def test_topology_page(self, client, regular_user):
        from tests.conftest import login
        login(client, 'testuser', 'userpass')
        resp = client.get('/network/topology')
        assert resp.status_code == 200

    def test_topology_data_json(self, client, regular_user):
        from tests.conftest import login
        login(client, 'testuser', 'userpass')
        resp = client.get('/network/topology/data')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'nodes' in data
        assert 'edges' in data
