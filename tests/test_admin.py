"""Tests for admin routes."""

from tests.conftest import login
from app.models import User


class TestAdminDashboard:
    def test_admin_dashboard_as_admin(self, client, admin_user):
        login(client, 'admin', 'adminpass')
        resp = client.get('/admin/')
        assert resp.status_code == 200

    def test_admin_dashboard_as_user_forbidden(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.get('/admin/')
        assert resp.status_code == 403


class TestUserManagement:
    def test_list_users(self, client, admin_user):
        login(client, 'admin', 'adminpass')
        resp = client.get('/admin/users')
        assert resp.status_code == 200
        assert b'admin' in resp.data

    def test_create_user(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        resp = client.post('/admin/users/create', data={
            'username': 'created_user',
            'email': 'created@test.com',
            'display_name': 'Created User',
            'password': 'password123',
            'password2': 'password123',
            'role': 'user',
        }, follow_redirects=True)
        assert resp.status_code == 200
        user = User.query.filter_by(username='created_user').first()
        assert user is not None

    def test_toggle_user(self, client, admin_user, regular_user, db):
        login(client, 'admin', 'adminpass')
        assert regular_user.is_active
        resp = client.post(f'/admin/users/{regular_user.id}/toggle', follow_redirects=True)
        assert resp.status_code == 200
        db.session.refresh(regular_user)
        assert not regular_user.is_active

    def test_delete_user(self, client, admin_user, db):
        user = User(username='deleteme', email='del@test.com', display_name='Del', role='user')
        user.set_password('pass')
        db.session.add(user)
        db.session.commit()
        uid = user.id
        login(client, 'admin', 'adminpass')
        resp = client.post(f'/admin/users/{uid}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert db.session.get(User, uid) is None

    def test_non_admin_cannot_manage_users(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.get('/admin/users')
        assert resp.status_code == 403


class TestAdminSettings:
    def test_settings_page(self, client, admin_user):
        login(client, 'admin', 'adminpass')
        resp = client.get('/admin/settings')
        assert resp.status_code == 200

    def test_save_smtp_settings(self, client, admin_user, db):
        login(client, 'admin', 'adminpass')
        resp = client.post('/admin/settings/smtp', data={
            'smtp_host': 'smtp.test.com',
            'smtp_port': '587',
            'smtp_use_tls': 'true',
            'smtp_username': 'testuser',
            'smtp_password': 'testpass',
            'smtp_sender': 'noreply@test.com',
        }, follow_redirects=True)
        assert resp.status_code == 200
        from app.models import AppSettings
        assert AppSettings.get('smtp_host') == 'smtp.test.com'


class TestAuditLog:
    def test_audit_log_page(self, client, admin_user):
        login(client, 'admin', 'adminpass')
        resp = client.get('/admin/audit-log')
        assert resp.status_code == 200
