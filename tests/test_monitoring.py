"""Tests for monitoring routes and ping logic."""

from datetime import datetime, timedelta, timezone

from tests.conftest import login
from app.models import PingResult, ResourceHost


class TestMonitoringDashboard:
    def test_dashboard_authenticated(self, client, regular_user):
        login(client, 'testuser', 'userpass')
        resp = client.get('/monitoring/dashboard')
        assert resp.status_code == 200


class TestStatusBadge:
    def test_status_badge(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.get(f'/monitoring/status/{sample_resource.id}')
        assert resp.status_code == 200


class TestPingHistory:
    def test_ping_history_json(self, client, regular_user, sample_host, db):
        login(client, 'testuser', 'userpass')
        # Add some ping results
        now = datetime.now(timezone.utc)
        for i in range(5):
            db.session.add(PingResult(
                host_id=sample_host.id,
                is_reachable=True,
                response_time_ms=10.0 + i,
                checked_at=now - timedelta(minutes=i),
            ))
        db.session.commit()

        resp = client.get(f'/monitoring/history/{sample_host.id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 5


class TestHealthHistory:
    def test_health_page(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.get(f'/monitoring/health/{sample_resource.id}')
        assert resp.status_code == 200

    def test_health_data_json(self, client, regular_user, sample_resource):
        login(client, 'testuser', 'userpass')
        resp = client.get(f'/monitoring/health/{sample_resource.id}/data')
        assert resp.status_code == 200
        assert resp.content_type.startswith('application/json')
