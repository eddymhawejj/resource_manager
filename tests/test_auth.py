"""Tests for authentication routes."""

from tests.conftest import login, logout
from app.models import User


class TestLogin:
    def test_login_page_renders(self, client):
        resp = client.get('/auth/login')
        assert resp.status_code == 200
        assert b'login' in resp.data.lower() or b'Log In' in resp.data or b'Sign' in resp.data

    def test_login_success(self, client, regular_user):
        resp = login(client, 'testuser', 'userpass')
        assert resp.status_code == 200
        assert b'Logged in successfully' in resp.data or b'testuser' in resp.data.lower()

    def test_login_wrong_password(self, client, regular_user):
        resp = login(client, 'testuser', 'wrongpass')
        assert b'Invalid' in resp.data

    def test_login_nonexistent_user(self, client):
        resp = login(client, 'nobody', 'nope')
        assert b'Invalid' in resp.data

    def test_login_deactivated_user(self, client, db, regular_user):
        regular_user.is_active = False
        db.session.commit()
        resp = login(client, 'testuser', 'userpass')
        assert b'deactivated' in resp.data.lower() or b'Contact' in resp.data

    def test_logout(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = logout(client)
        assert resp.status_code == 200
        assert b'logged out' in resp.data.lower() or b'login' in resp.data.lower()


class TestRegister:
    def test_register_page_renders(self, client):
        resp = client.get('/auth/register')
        assert resp.status_code == 200

    def test_register_success(self, client, db):
        resp = client.post('/auth/register', data={
            'username': 'newuser',
            'email': 'new@test.com',
            'display_name': 'New User',
            'password': 'newpass123',
            'password2': 'newpass123',
        }, follow_redirects=True)
        assert resp.status_code == 200
        user = User.query.filter_by(username='newuser').first()
        assert user is not None
        assert user.check_password('newpass123')
        assert user.role == 'user'


class TestAuthRequired:
    def test_protected_route_redirects_to_login(self, client):
        resp = client.get('/bookings/')
        assert resp.status_code == 302 or resp.status_code == 401
        if resp.status_code == 302:
            assert 'login' in resp.headers.get('Location', '').lower()
