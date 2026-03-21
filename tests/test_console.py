"""Tests for console routes (file management, access control, token encryption)."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.conftest import login
from app.models import (
    AccessPoint, Resource, Booking, can_user_access,
)


class TestConsoleSession:
    def test_session_requires_auth(self, client, db, sample_resource):
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='rdp',
            hostname='10.0.0.1', is_enabled=True,
        )
        ap.password = 'secret'
        db.session.add(ap)
        db.session.commit()
        resp = client.get(f'/console/{ap.id}')
        assert resp.status_code in (302, 401)

    def test_session_disabled_ap_404(self, client, admin_user, db, sample_resource):
        login(client, 'admin', 'adminpass')
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='ssh',
            hostname='10.0.0.1', is_enabled=False,
        )
        db.session.add(ap)
        db.session.commit()
        resp = client.get(f'/console/{ap.id}')
        assert resp.status_code == 404

    def test_session_renders_for_admin(self, client, admin_user, db, sample_resource):
        login(client, 'admin', 'adminpass')
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='rdp',
            hostname='10.0.0.1', is_enabled=True,
        )
        ap.password = 'secret'
        db.session.add(ap)
        db.session.commit()
        resp = client.get(f'/console/{ap.id}')
        assert resp.status_code == 200

    def test_session_denied_without_booking(self, client, regular_user, db, sample_resource):
        login(client, 'testuser', 'userpass')
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='ssh',
            hostname='10.0.0.1', is_enabled=True,
        )
        db.session.add(ap)
        db.session.commit()
        resp = client.get(f'/console/{ap.id}')
        assert resp.status_code == 403

    def test_session_allowed_with_active_booking(self, client, regular_user, db, sample_resource):
        login(client, 'testuser', 'userpass')
        now = datetime.now(timezone.utc)
        booking = Booking(
            resource_id=sample_resource.id, user_id=regular_user.id,
            title='Active', start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1), status='confirmed',
        )
        ap = AccessPoint(
            resource_id=sample_resource.id, protocol='rdp',
            hostname='10.0.0.1', is_enabled=True,
        )
        ap.password = 'pass'
        db.session.add_all([booking, ap])
        db.session.commit()
        resp = client.get(f'/console/{ap.id}')
        assert resp.status_code == 200


class TestConsoleFileManagement:
    def test_list_files_admin(self, client, admin_user, db, sample_resource, app):
        login(client, 'admin', 'adminpass')
        # Create a temp file in the drive directory
        drive_base = app.config.get('DRIVE_PATH', os.path.join(app.root_path, '..', 'data', 'drive'))
        drive_dir = os.path.join(drive_base, str(sample_resource.id))
        os.makedirs(drive_dir, exist_ok=True)
        test_file = os.path.join(drive_dir, 'test.txt')
        with open(test_file, 'w') as f:
            f.write('hello')
        try:
            resp = client.get(f'/console/{sample_resource.id}/files')
            assert resp.status_code == 200
            data = resp.get_json()
            assert any(f['name'] == 'test.txt' for f in data)
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)
            if os.path.isdir(drive_dir):
                os.rmdir(drive_dir)

    def test_list_files_denied_without_access(self, client, regular_user, db, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.get(f'/console/{sample_resource.id}/files')
        assert resp.status_code == 403

    def test_delete_file_denied_without_access(self, client, regular_user, db, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.delete(f'/console/{sample_resource.id}/files/test.txt')
        assert resp.status_code == 403


class TestTokenEncryption:
    def test_encrypt_token_produces_base64(self):
        from app.console.token import encrypt_token
        payload = {
            'connection': {
                'type': 'rdp',
                'settings': {'hostname': '10.0.0.1', 'port': '3389'},
            }
        }
        token = encrypt_token(payload, 'test-secret-key-1234567890123456')
        assert isinstance(token, str)
        # Should be valid base64
        import base64
        decoded = base64.b64decode(token)
        import json
        outer = json.loads(decoded)
        assert 'iv' in outer
        assert 'value' in outer

    def test_encrypt_token_different_each_time(self):
        """Each call uses a random IV so tokens differ even for same payload."""
        from app.console.token import encrypt_token
        payload = {'connection': {'type': 'ssh', 'settings': {}}}
        t1 = encrypt_token(payload, 'key12345678901234567890123456789')
        t2 = encrypt_token(payload, 'key12345678901234567890123456789')
        assert t1 != t2

    def test_encrypt_token_short_key_padded(self):
        """Short keys should be zero-padded to 32 bytes without error."""
        from app.console.token import encrypt_token
        payload = {'connection': {'type': 'rdp', 'settings': {}}}
        token = encrypt_token(payload, 'short')
        assert isinstance(token, str)
