"""Shared fixtures for the test suite."""

import os
import tempfile

import pytest

# Force test config before anything imports the app
os.environ['TESTING'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ['SECRET_KEY'] = 'test-secret-key'


from app import create_app
from app.extensions import db as _db
from app.models import User, Resource, ResourceHost, Booking, PingResult, Vlan, Subnet, AppSettings


class TestConfig:
    TESTING = True
    SECRET_KEY = 'test-secret-key'
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SERVER_NAME = 'localhost'
    MAIL_SUPPRESS_SEND = True
    PING_INTERVAL_SECONDS = 0  # Disable scheduler in tests
    LOGIN_DISABLED = False


@pytest.fixture(scope='session')
def app():
    """Create the Flask application for testing (once per session)."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    config = TestConfig()
    config.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    config.DATABASE_URL = f'sqlite:///{db_path}'

    application = create_app(config)

    yield application

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture(autouse=True)
def db(app):
    """Create fresh tables for each test, roll back afterwards."""
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def admin_user(db):
    """Create and return an admin user."""
    user = User(
        username='admin',
        email='admin@test.com',
        display_name='Admin User',
        role='admin',
        auth_type='local',
    )
    user.set_password('adminpass')
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def regular_user(db):
    """Create and return a regular user."""
    user = User(
        username='testuser',
        email='user@test.com',
        display_name='Test User',
        role='user',
        auth_type='local',
    )
    user.set_password('userpass')
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def sample_resource(db):
    """Create a sample testbed resource."""
    resource = Resource(
        name='Test Testbed',
        description='A test resource',
        resource_type='testbed',
        is_active=True,
    )
    db.session.add(resource)
    db.session.commit()
    return resource


@pytest.fixture
def sample_host(db, sample_resource):
    """Create a sample resource host."""
    host = ResourceHost(
        resource_id=sample_resource.id,
        address='192.168.1.10',
        label='Main Host',
        critical=True,
    )
    db.session.add(host)
    db.session.commit()
    return host


def login(client, username, password):
    """Helper to log in via the test client."""
    return client.post('/auth/login', data={
        'username': username,
        'password': password,
        'auth_type': 'local',
    }, follow_redirects=True)


def logout(client):
    """Helper to log out via the test client."""
    return client.get('/auth/logout', follow_redirects=True)
