"""Tests for ping service with mocked ICMP."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models import Resource, ResourceHost, PingResult, MaintenanceWindow


class TestPingHost:
    @patch('app.monitoring.ping_service.subprocess.run')
    def test_ping_success(self, mock_run, app):
        mock_run.return_value = type('Result', (), {
            'returncode': 0,
            'stdout': 'PING host (192.168.1.1) 56(84) bytes\n64 bytes: time=5.23 ms',
            'stderr': '',
        })()
        from app.monitoring.ping_service import ping_host
        reachable, rtt, resolved = ping_host('192.168.1.1')
        assert reachable is True
        assert rtt == 5.23
        assert resolved == '192.168.1.1'

    @patch('app.monitoring.ping_service.subprocess.run')
    def test_ping_failure(self, mock_run, app):
        mock_run.return_value = type('Result', (), {
            'returncode': 1,
            'stdout': 'PING host (10.0.0.1) 56(84) bytes\n100% packet loss',
            'stderr': '',
        })()
        from app.monitoring.ping_service import ping_host
        reachable, rtt, resolved = ping_host('10.0.0.1')
        assert reachable is False
        assert rtt is None
        assert resolved == '10.0.0.1'

    @patch('app.monitoring.ping_service.subprocess.run')
    def test_ping_timeout(self, mock_run, app):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='ping', timeout=2)
        from app.monitoring.ping_service import ping_host
        reachable, rtt, resolved = ping_host('10.0.0.1')
        assert reachable is False
        assert rtt is None
        assert resolved is None

    def test_ping_empty_host(self, app):
        from app.monitoring.ping_service import ping_host
        reachable, rtt, resolved = ping_host('')
        assert reachable is False

    def test_ping_none_host(self, app):
        from app.monitoring.ping_service import ping_host
        reachable, rtt, resolved = ping_host(None)
        assert reachable is False


class TestPingAllResources:
    @patch('app.monitoring.ping_service.ping_host')
    @patch('app.monitoring.ping_service.check_and_send_alerts', create=True)
    def test_pings_active_hosts(self, mock_alerts, mock_ping, app, db):
        with app.app_context():
            mock_ping.return_value = (True, 10.0, '10.0.0.1')
            resource = Resource(name='Ping Test', resource_type='testbed', is_active=True)
            db.session.add(resource)
            db.session.commit()
            host = ResourceHost(resource_id=resource.id, address='10.0.0.1', label='H', critical=True)
            db.session.add(host)
            db.session.commit()

            from app.monitoring.ping_service import ping_all_resources
            ping_all_resources(app)

            mock_ping.assert_called_once_with('10.0.0.1', 2)
            result = PingResult.query.filter_by(host_id=host.id).first()
            assert result is not None
            assert result.is_reachable is True

    @patch('app.monitoring.ping_service.ping_host')
    @patch('app.monitoring.ping_service.check_and_send_alerts', create=True)
    def test_skips_maintenance_hosts(self, mock_alerts, mock_ping, app, db):
        with app.app_context():
            resource = Resource(name='MW Test', resource_type='testbed', is_active=True)
            db.session.add(resource)
            db.session.commit()
            host = ResourceHost(resource_id=resource.id, address='10.0.0.2', label='H', critical=True)
            db.session.add(host)
            now = datetime.now(timezone.utc)
            mw = MaintenanceWindow(
                resource_id=resource.id, title='MW',
                start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=1),
            )
            db.session.add(mw)
            db.session.commit()

            from app.monitoring.ping_service import ping_all_resources
            ping_all_resources(app)

            mock_ping.assert_not_called()

    @patch('app.monitoring.ping_service.ping_host')
    @patch('app.monitoring.ping_service.check_and_send_alerts', create=True)
    def test_prunes_old_results(self, mock_alerts, mock_ping, app, db):
        with app.app_context():
            mock_ping.return_value = (True, 5.0, '10.0.0.3')
            resource = Resource(name='Prune Test', resource_type='testbed', is_active=True)
            db.session.add(resource)
            db.session.commit()
            host = ResourceHost(resource_id=resource.id, address='10.0.0.3', label='H', critical=True)
            db.session.add(host)
            db.session.commit()

            # Pre-fill with history_limit results
            app.config['PING_HISTORY_LIMIT'] = 5
            for i in range(6):
                db.session.add(PingResult(
                    host_id=host.id, is_reachable=True, response_time_ms=1.0,
                    checked_at=datetime.now(timezone.utc) - timedelta(minutes=i),
                ))
            db.session.commit()

            from app.monitoring.ping_service import ping_all_resources
            ping_all_resources(app)

            count = PingResult.query.filter_by(host_id=host.id).count()
            assert count <= 6  # 5 limit + 1 new - pruned old
