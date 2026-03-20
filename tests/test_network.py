"""Tests for network (VLAN/subnet) routes."""

from tests.conftest import login
from app.models import Vlan, Subnet


class TestNetworkOverview:
    def test_network_overview(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.get('/network/')
        assert resp.status_code == 200


class TestVlanCRUD:
    def test_create_vlan(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        resp = client.post('/network/vlans/add', data={
            'number': 100,
            'name': 'Test VLAN',
            'description': 'A test VLAN',
        }, follow_redirects=True)
        assert resp.status_code == 200
        vlan = Vlan.query.filter_by(number=100).first()
        assert vlan is not None
        assert vlan.name == 'Test VLAN'

    def test_create_vlan_non_admin(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.post('/network/vlans/add', data={
            'number': 200, 'name': 'Nope',
        })
        assert resp.status_code == 403

    def test_vlan_detail(self, client, regular_user, db):
        vlan = Vlan(number=101, name='Detail VLAN')
        db.session.add(vlan)
        db.session.commit()
        login(client, 'testuser', 'userpass')
        resp = client.get(f'/network/vlans/{vlan.id}')
        assert resp.status_code == 200
        assert b'Detail VLAN' in resp.data

    def test_delete_vlan(self, client, admin_user, db):
        vlan = Vlan(number=999, name='Delete Me')
        db.session.add(vlan)
        db.session.commit()
        vid = vlan.id
        login(client, 'admin', 'adminpass')
        resp = client.post(f'/network/vlans/{vid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(Vlan, vid) is None


class TestSubnetCRUD:
    def test_create_subnet(self, client, admin_user, db):
        vlan = Vlan(number=400, name='V400')
        db.session.add(vlan)
        db.session.commit()
        login(client, 'admin', 'adminpass')
        resp = client.post('/network/subnets/add', data={
            'vlan_id': vlan.id,
            'cidr': '10.10.0.0/24',
            'name': 'Test Subnet',
            'gateway': '10.10.0.1',
            'description': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        subnet = Subnet.query.filter_by(cidr='10.10.0.0/24').first()
        assert subnet is not None

    def test_delete_subnet(self, client, admin_user, db):
        vlan = Vlan(number=401, name='V401')
        db.session.add(vlan)
        db.session.commit()
        subnet = Subnet(vlan_id=vlan.id, cidr='10.20.0.0/24', name='Del Sub')
        db.session.add(subnet)
        db.session.commit()
        sid = subnet.id
        login(client, 'admin', 'adminpass')
        resp = client.post(f'/network/subnets/{sid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(Subnet, sid) is None
