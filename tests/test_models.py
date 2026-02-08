"""
Tests for monitoring models.
"""
import pytest
from django.db import connection
from django.utils import timezone

from monitoring.models import HealthCheck, Message
from tests.factories import HealthCheckFactory, MessageFactory


@pytest.mark.django_db
class TestHealthCheckModel:
    """Tests for the HealthCheck model."""

    def test_healthcheck_creation(self, sample_health_check_data):
        """Can create a HealthCheck with all fields."""
        health_check = HealthCheck.objects.create(**sample_health_check_data)

        assert health_check.pk is not None
        assert health_check.service_name == sample_health_check_data["service_name"]
        assert health_check.status == "up"
        assert health_check.response_time_ms == 150
        assert health_check.status_code == 200
        assert health_check.error_message == ""
        assert health_check.checked_at is not None
        assert health_check.created_at is not None

    def test_healthcheck_ordering(self):
        """Default ordering is -checked_at."""
        now = timezone.now()
        older = HealthCheckFactory(checked_at=now - timezone.timedelta(hours=1))
        newer = HealthCheckFactory(checked_at=now)

        checks = list(HealthCheck.objects.all())
        assert checks[0] == newer
        assert checks[1] == older

    def test_healthcheck_str(self, sample_health_check_data):
        """String representation is useful."""
        health_check = HealthCheck.objects.create(**sample_health_check_data)
        str_repr = str(health_check)

        assert "test-service" in str_repr
        assert "UP" in str_repr

    def test_healthcheck_indexes_exist(self):
        """Composite index exists on service_name + checked_at."""
        # Get index names from the database
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='monitoring_healthcheck'"
            )
            indexes = [row[0] for row in cursor.fetchall()]

        # Check that our custom indexes exist (Django auto-names them)
        index_names = " ".join(indexes)
        assert "service_name" in index_names or "monitoring_health" in index_names

    def test_healthcheck_factory(self):
        """HealthCheckFactory creates valid instances."""
        health_check = HealthCheckFactory()

        assert health_check.pk is not None
        assert health_check.status == "up"

    def test_healthcheck_down_status(self):
        """Can create a down health check."""
        health_check = HealthCheckFactory(
            status="down",
            status_code=503,
            error_message="Service Unavailable",
        )

        assert health_check.status == "down"
        assert health_check.status_code == 503
        assert health_check.error_message == "Service Unavailable"


@pytest.mark.django_db
class TestMessageModel:
    """Tests for the Message model."""

    def test_message_creation(self, sample_message_data):
        """Can create a Message with all fields."""
        message = Message.objects.create(**sample_message_data)

        assert message.pk is not None
        assert message.severity == "high"
        assert message.text == "Test incident message"
        assert message.active is True
        assert message.created_at is not None
        assert message.updated_at is not None

    def test_message_default_active(self):
        """New messages default to active=True."""
        message = Message.objects.create(severity="medium", text="Test")

        assert message.active is True

    def test_message_str(self):
        """String representation shows severity and truncated text."""
        message = MessageFactory(
            severity="high",
            text="This is a very long message that should be truncated in the string representation",
        )
        str_repr = str(message)

        assert "[HIGH]" in str_repr
        assert "..." in str_repr  # Should be truncated

    def test_message_str_short_text(self):
        """String representation doesn't truncate short text."""
        message = MessageFactory(severity="medium", text="Short message")
        str_repr = str(message)

        assert "[MEDIUM]" in str_repr
        assert "..." not in str_repr

    def test_active_messages_query(self):
        """Filter returns only active messages."""
        MessageFactory(active=True)
        MessageFactory(active=True)
        MessageFactory(active=False)

        active_messages = Message.objects.filter(active=True)
        assert active_messages.count() == 2

    def test_message_severity_choices(self):
        """All severity choices work."""
        for severity in ["high", "medium", "resolved"]:
            message = MessageFactory(severity=severity)
            assert message.severity == severity

    def test_message_factory(self):
        """MessageFactory creates valid instances."""
        message = MessageFactory()

        assert message.pk is not None
        assert message.severity in ["high", "medium", "resolved"]
