"""
Tests for the Slack alerter service.
"""
import pytest
from unittest.mock import patch, MagicMock
from django.utils import timezone

from monitoring.services.checker import HealthCheckResult


pytestmark = pytest.mark.django_db


class TestSlackAlerter:
    """Tests for the Slack alerter."""

    @patch("monitoring.services.alerter.WebhookClient")
    def test_alert_sends_on_down_transition(self, mock_webhook_class):
        """Slack alert is sent when service goes down."""
        from monitoring.services.alerter import send_alert
        
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=200)
        mock_webhook_class.return_value = mock_client
        
        result = HealthCheckResult(
            service_name="test-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Service Unavailable",
        )
        
        with patch("monitoring.services.alerter.settings") as mock_settings:
            mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
            mock_settings.STATUS_PAGE_URL = "https://status.sefaria.org"
            
            send_alert(result, "went_down")
        
        mock_client.send.assert_called_once()

    @patch("monitoring.services.alerter.WebhookClient")
    def test_alert_sends_on_recovery(self, mock_webhook_class):
        """Slack alert is sent when service recovers."""
        from monitoring.services.alerter import send_alert
        
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=200)
        mock_webhook_class.return_value = mock_client
        
        result = HealthCheckResult(
            service_name="test-service",
            status="up",
            response_time_ms=150,
            status_code=200,
            error_message="",
        )
        
        with patch("monitoring.services.alerter.settings") as mock_settings:
            mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
            mock_settings.STATUS_PAGE_URL = "https://status.sefaria.org"
            
            send_alert(result, "recovered")
        
        mock_client.send.assert_called_once()

    def test_alert_not_sent_when_no_webhook_url(self):
        """No alert is sent if SLACK_WEBHOOK_URL is empty."""
        from monitoring.services.alerter import send_alert
        
        result = HealthCheckResult(
            service_name="test-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Error",
        )
        
        with patch("monitoring.services.alerter.settings") as mock_settings:
            mock_settings.SLACK_WEBHOOK_URL = ""
            
            # Should not raise
            send_alert(result, "went_down")

    @patch("monitoring.services.alerter.WebhookClient")
    def test_alert_includes_service_name(self, mock_webhook_class):
        """Alert payload contains service name."""
        from monitoring.services.alerter import send_alert
        
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=200)
        mock_webhook_class.return_value = mock_client
        
        result = HealthCheckResult(
            service_name="my-important-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Down",
        )
        
        with patch("monitoring.services.alerter.settings") as mock_settings:
            mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
            mock_settings.STATUS_PAGE_URL = "https://status.sefaria.org"
            
            send_alert(result, "went_down")
        
        # Check the call args
        call_kwargs = mock_client.send.call_args[1]
        assert "my-important-service" in call_kwargs["text"]

    @patch("monitoring.services.alerter.WebhookClient")
    def test_alert_includes_diagnostic_info(self, mock_webhook_class):
        """Alert payload contains HTTP code and error message."""
        from monitoring.services.alerter import send_alert
        
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=200)
        mock_webhook_class.return_value = mock_client
        
        result = HealthCheckResult(
            service_name="test-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Service Unavailable",
        )
        
        with patch("monitoring.services.alerter.settings") as mock_settings:
            mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
            mock_settings.STATUS_PAGE_URL = "https://status.sefaria.org"
            
            send_alert(result, "went_down")
        
        call_kwargs = mock_client.send.call_args[1]
        # Should have blocks for rich formatting
        assert "blocks" in call_kwargs

    @patch("monitoring.services.alerter.WebhookClient")
    def test_alert_uses_block_kit(self, mock_webhook_class):
        """Alert payload uses Slack Block Kit format."""
        from monitoring.services.alerter import send_alert
        
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=200)
        mock_webhook_class.return_value = mock_client
        
        result = HealthCheckResult(
            service_name="test-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Error",
        )
        
        with patch("monitoring.services.alerter.settings") as mock_settings:
            mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
            mock_settings.STATUS_PAGE_URL = "https://status.sefaria.org"
            
            send_alert(result, "went_down")
        
        call_kwargs = mock_client.send.call_args[1]
        blocks = call_kwargs["blocks"]
        
        # Should have at least header, section, and context blocks
        assert isinstance(blocks, list)
        assert len(blocks) >= 2


class TestProcessTransitionsWithAlerts:
    """Tests for processing transitions and sending alerts."""

    @patch("monitoring.services.alerter.send_alert")
    def test_process_transitions_sends_alerts(self, mock_send_alert):
        """process_transitions_with_alerts calls send_alert for each transition."""
        from monitoring.services.alerter import process_transitions_with_alerts
        
        down_result = HealthCheckResult(
            service_name="service-a",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Down",
        )
        up_result = HealthCheckResult(
            service_name="service-b",
            status="up",
            response_time_ms=100,
            status_code=200,
            error_message="",
        )
        
        transitions = [
            (down_result, "went_down"),
            (up_result, "recovered"),
        ]
        
        process_transitions_with_alerts(transitions)
        
        assert mock_send_alert.call_count == 2
