import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///resource_manager.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    @staticmethod
    def _engine_options():
        uri = os.environ.get('DATABASE_URL', 'sqlite:///resource_manager.db')
        opts = {'pool_pre_ping': True}
        if uri.startswith('postgresql'):
            opts.update({
                'pool_size': int(os.environ.get('PG_POOL_SIZE', 5)),
                'max_overflow': int(os.environ.get('PG_MAX_OVERFLOW', 10)),
            })
        return opts

    SQLALCHEMY_ENGINE_OPTIONS = _engine_options.__func__()
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB max upload

    # Mail
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.example.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@example.com')

    # LDAP
    LDAP_ENABLED = os.environ.get('LDAP_ENABLED', 'false').lower() == 'true'
    LDAP_URL = os.environ.get('LDAP_URL', 'ldap://ldap.example.com')
    LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', 'dc=example,dc=com')
    LDAP_USER_DN = os.environ.get('LDAP_USER_DN', 'ou=users')
    LDAP_BIND_DN = os.environ.get('LDAP_BIND_DN', '')
    LDAP_BIND_PASSWORD = os.environ.get('LDAP_BIND_PASSWORD', '')

    # Guacamole (guacd)
    GUACD_HOST = os.environ.get('GUACD_HOST', 'localhost')
    GUACD_PORT = int(os.environ.get('GUACD_PORT', 4822))

    # guacamole-lite (native WebSocket↔guacd relay)
    GUACLITE_URL = os.environ.get('GUACLITE_URL', 'ws://localhost:8080')
    GUACLITE_SECRET_KEY = os.environ.get('GUACLITE_SECRET_KEY',
                                         '4BQXC6JAPXst3EDAHhjpJRa2bNGi3lON')

    # Python WebSocket relay fallback (disable when using guacamole-lite to
    # avoid silent fallback masking connectivity issues)
    GUAC_PYTHON_RELAY_ENABLED = os.environ.get(
        'GUAC_PYTHON_RELAY_ENABLED', 'false').lower() == 'true'

    # flask-sock: accept the 'guacamole' WebSocket subprotocol
    SOCK_SERVER_OPTIONS = {'subprotocols': ['guacamole']}

    # File transfer shared drive
    DRIVE_PATH = os.environ.get('DRIVE_PATH', os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'drive'))

    # Monitoring
    PING_INTERVAL_SECONDS = int(os.environ.get('PING_INTERVAL_SECONDS', 60))
    PING_TIMEOUT_SECONDS = int(os.environ.get('PING_TIMEOUT_SECONDS', 2))
    PING_HISTORY_LIMIT = int(os.environ.get('PING_HISTORY_LIMIT', 100))
