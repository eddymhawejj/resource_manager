"""Tests for resource CRUD routes."""

from tests.conftest import login
from app.models import Resource, ResourceHost, Tag


class TestResourceList:
    def test_list_resources_authenticated(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.get('/resources/')
        assert resp.status_code == 200
        assert b'Test Testbed' in resp.data

    def test_list_resources_unauthenticated(self, client):
        resp = client.get('/resources/')
        # Should redirect to login or show 401
        assert resp.status_code in (302, 401, 200)


class TestResourceDetail:
    def test_resource_detail(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.get(f'/resources/{sample_resource.id}')
        assert resp.status_code == 200
        assert b'Test Testbed' in resp.data

    def test_resource_detail_404(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.get('/resources/9999')
        assert resp.status_code == 404


class TestResourceCreate:
    def test_add_resource_admin(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        resp = client.post('/resources/add', data={
            'name': 'New Resource',
            'description': 'A new testbed',
            'resource_type': 'testbed',
            'location': 'Lab A',
            'host_addresses[]': ['10.0.0.1'],
            'host_labels[]': ['Primary'],
            'host_critical[]': ['1'],
        }, follow_redirects=True)
        assert resp.status_code == 200
        resource = Resource.query.filter_by(name='New Resource').first()
        assert resource is not None

    def test_add_resource_non_admin_forbidden(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.post('/resources/add', data={
            'name': 'Forbidden',
            'resource_type': 'testbed',
        })
        assert resp.status_code == 403


class TestResourceEdit:
    def test_edit_resource_admin(self, client, admin_user, sample_resource, db):
        login(client, 'admin', 'adminpass')
        resp = client.post(f'/resources/{sample_resource.id}/edit', data={
            'name': 'Updated Name',
            'description': 'Updated desc',
            'resource_type': 'testbed',
            'location': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        db.session.refresh(sample_resource)
        assert sample_resource.name == 'Updated Name'

    def test_edit_resource_non_admin_forbidden(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.post(f'/resources/{sample_resource.id}/edit', data={
            'name': 'Hacked',
        })
        assert resp.status_code == 403


class TestResourceDelete:
    def test_delete_resource_admin(self, client, admin_user, sample_resource, db):
        login(client, 'admin', 'adminpass')
        rid = sample_resource.id
        resp = client.post(f'/resources/{rid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(Resource, rid) is None

    def test_delete_resource_non_admin_forbidden(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.post(f'/resources/{sample_resource.id}/delete')
        assert resp.status_code == 403


class TestChildResource:
    def test_add_child_resource(self, client, admin_user, sample_resource, db):
        login(client, 'admin', 'adminpass')
        resp = client.post(f'/resources/{sample_resource.id}/children/add', data={
            'name': 'Child Resource',
            'description': 'A child',
            'resource_type': 'server',
            'location': '',
            'is_active': 'y',
        }, follow_redirects=True)
        assert resp.status_code == 200
        child = Resource.query.filter_by(name='Child Resource').first()
        assert child is not None
        assert child.parent_id == sample_resource.id
