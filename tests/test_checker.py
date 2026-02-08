"""
Tests for the health checker service.
"""
import pytest
from unittest.mock import patch, MagicMock

import httpx


# Import after Django is set up by pytest-django
pytestmark = pytest.mark.django_db


class TestHealthCheckResult:
    """Tests for the HealthCheckResult dataclass."""

    def test_result_creation_success(self):
        """Can create a successful health check result."""
        from monitoring.services.checker import HealthCheckResult
        
        result = HealthCheckResult(
            service_name="test-service",
            status="up",
            response_time_ms=150,
            status_code=200,
            error_message="",
        )
        
        assert result.service_name == "test-service"
        assert result.status == "up"
        assert result.response_time_ms == 150
        assert result.status_code == 200
        assert result.error_message == ""
        assert result.is_up is True

    def test_result_creation_failure(self):
        """Can create a failed health check result."""
        from monitoring.services.checker import HealthCheckResult
        
        result = HealthCheckResult(
            service_name="test-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Service Unavailable",
        )
        
        assert result.status == "down"
        assert result.is_up is False


class TestCheckService:
    """Tests for the check_service function."""

    @patch("monitoring.services.checker.httpx.Client")
    def test_check_service_success_get(self, mock_client_class):
        """Mock httpx GET request returns 200, result is 'up'."""
        from monitoring.services.checker import check_service
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.150
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config)
        
        assert result.status == "up"
        assert result.status_code == 200
        assert result.response_time_ms == 150
        assert result.error_message == ""

    @patch("monitoring.services.checker.httpx.Client")
    def test_check_service_failure_status_code(self, mock_client_class):
        """Mock httpx returns 503, result is 'down'."""
        from monitoring.services.checker import check_service
        
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.elapsed.total_seconds.return_value = 0.200
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config, max_retries=1)
        
        assert result.status == "down"
        assert result.status_code == 503
        assert "Expected 200" in result.error_message

    @patch("monitoring.services.checker.httpx.Client")
    def test_check_service_timeout(self, mock_client_class):
        """Mock httpx timeout, result is 'down'."""
        from monitoring.services.checker import check_service
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.side_effect = httpx.TimeoutException("Connection timed out")
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config, max_retries=1)
        
        assert result.status == "down"
        assert result.status_code is None
        assert "timed out" in result.error_message.lower()

    @patch("monitoring.services.checker.httpx.Client")
    def test_check_service_connection_error(self, mock_client_class):
        """Mock connection refused, result is 'down'."""
        from monitoring.services.checker import check_service
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config, max_retries=1)
        
        assert result.status == "down"
        assert "Connection" in result.error_message

    @patch("monitoring.services.checker.httpx.Client")
    def test_check_service_post_method(self, mock_client_class):
        """Mock httpx POST request works correctly."""
        from monitoring.services.checker import check_service
        
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.elapsed.total_seconds.return_value = 0.100
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "linker",
            "url": "https://example.com/api/find-refs",
            "method": "POST",
            "expected_status": 202,
            "timeout": 10,
            "request_body": {"text": "test"},
        }
        
        result = check_service(config)
        
        assert result.status == "up"
        assert result.status_code == 202
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert call_args[0][0] == "POST"


class TestCheckServiceWithRetry:
    """Tests for retry logic in check_service."""

    @patch("monitoring.services.checker.httpx.Client")
    def test_retry_eventual_success(self, mock_client_class):
        """First 2 attempts fail, third succeeds -> 'up'."""
        from monitoring.services.checker import check_service
        
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.elapsed.total_seconds.return_value = 0.15
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.side_effect = [
            httpx.ConnectError("fail 1"),
            httpx.ConnectError("fail 2"),
            mock_response_success,
        ]
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config, max_retries=3, retry_delay=0.01)
        
        assert result.status == "up"
        assert mock_client.request.call_count == 3

    @patch("monitoring.services.checker.httpx.Client")
    def test_retry_all_fail(self, mock_client_class):
        """All 3 retries fail -> 'down'."""
        from monitoring.services.checker import check_service
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config, max_retries=3, retry_delay=0.01)
        
        assert result.status == "down"
        assert mock_client.request.call_count == 3


class TestCheckServicePersistence:
    """Tests for persisting health check results to database."""

    @patch("monitoring.services.checker.httpx.Client")
    def test_check_persists_to_db(self, mock_client_class):
        """After check, a HealthCheck record exists in database."""
        from monitoring.services.checker import check_service
        from monitoring.models import HealthCheck
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.150
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "persist-test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config, persist=True)
        
        # Verify record was created
        health_check = HealthCheck.objects.filter(service_name="persist-test-service").first()
        assert health_check is not None
        assert health_check.status == "up"
        assert health_check.response_time_ms == 150
        assert health_check.status_code == 200

    @patch("monitoring.services.checker.httpx.Client")
    def test_check_measures_response_time(self, mock_client_class):
        """response_time_ms is populated from httpx elapsed time."""
        from monitoring.services.checker import check_service
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.elapsed.total_seconds.return_value = 0.234
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client
        
        config = {
            "name": "test-service",
            "url": "https://example.com/healthz",
            "method": "GET",
            "expected_status": 200,
            "timeout": 10,
        }
        
        result = check_service(config)
        
        assert result.response_time_ms == 234


class TestCheckAllServices:
    """Tests for check_all_services function."""

    @patch("monitoring.services.checker.check_service")
    def test_check_all_services(self, mock_check_service):
        """check_all_services calls check_service for each configured service."""
        from monitoring.services.checker import check_all_services, HealthCheckResult
        
        mock_check_service.return_value = HealthCheckResult(
            service_name="test",
            status="up",
            response_time_ms=100,
            status_code=200,
            error_message="",
        )
        
        results = check_all_services()
        
        # Should check all services from settings (test settings has 1 service)
        assert mock_check_service.call_count >= 1
        assert len(results) >= 1
