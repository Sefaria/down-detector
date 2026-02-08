"""
Pytest configuration and shared fixtures.
"""
import pytest
from django.utils import timezone


@pytest.fixture
def sample_health_check_data():
    """Sample data for creating HealthCheck instances."""
    return {
        "service_name": "test-service",
        "status": "up",
        "response_time_ms": 150,
        "status_code": 200,
        "error_message": "",
        "checked_at": timezone.now(),
    }


@pytest.fixture
def sample_message_data():
    """Sample data for creating Message instances."""
    return {
        "severity": "high",
        "text": "Test incident message",
        "active": True,
    }
