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


class TestDowntimeDuration:
    """Tests for downtime duration in recovery alerts."""

    def test_recovery_alert_includes_downtime_field(self):
        """Recovery alert Block Kit payload contains a Downtime field."""
        from monitoring.services.alerter import _build_recovery_alert

        result = HealthCheckResult(
            service_name="test-service",
            status="up",
            response_time_ms=150,
            status_code=200,
            error_message="",
        )

        with patch("monitoring.services.alerter._get_downtime_duration", return_value="5m 30s"):
            with patch("monitoring.services.alerter.settings") as mock_settings:
                mock_settings.STATUS_PAGE_URL = "https://status.sefaria.org"
                blocks = _build_recovery_alert(result)

        # Find the section block and check for Downtime field
        section = next(b for b in blocks if b["type"] == "section")
        field_texts = [f["text"] for f in section["fields"]]
        assert any("*Downtime:*" in t for t in field_texts)
        assert any("5m 30s" in t for t in field_texts)

    def test_downtime_duration_formats_minutes_and_seconds(self):
        """Duration under 1 hour shows minutes and seconds."""
        from monitoring.services.alerter import _get_downtime_duration
        from monitoring.models import HealthCheck

        now = timezone.now()
        # Create 3 consecutive down records spanning 5 minutes
        HealthCheck.objects.create(
            service_name="fmt-test",
            status="up",
            checked_at=now - timezone.timedelta(minutes=10),
        )
        HealthCheck.objects.create(
            service_name="fmt-test",
            status="down",
            checked_at=now - timezone.timedelta(minutes=5),
        )
        HealthCheck.objects.create(
            service_name="fmt-test",
            status="down",
            checked_at=now - timezone.timedelta(minutes=3),
        )

        duration = _get_downtime_duration("fmt-test")
        assert "m" in duration
        assert "h" not in duration

    def test_downtime_duration_formats_hours(self):
        """Duration over 1 hour shows hours and minutes."""
        from monitoring.services.alerter import _get_downtime_duration
        from monitoring.models import HealthCheck

        now = timezone.now()
        HealthCheck.objects.create(
            service_name="hours-test",
            status="up",
            checked_at=now - timezone.timedelta(hours=3),
        )
        HealthCheck.objects.create(
            service_name="hours-test",
            status="down",
            checked_at=now - timezone.timedelta(hours=2),
        )

        duration = _get_downtime_duration("hours-test")
        assert "h" in duration

    def test_downtime_duration_unknown_when_no_records(self):
        """Returns 'Unknown' when no down records exist."""
        from monitoring.services.alerter import _get_downtime_duration

        duration = _get_downtime_duration("nonexistent-service")
        assert duration == "Unknown"
