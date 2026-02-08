"""
Tests for the state transition tracker.
"""
import pytest
from unittest.mock import patch, MagicMock
from django.utils import timezone

from tests.factories import HealthCheckFactory


pytestmark = pytest.mark.django_db


class TestStateTracker:
    """Tests for the StateTracker class."""

    def test_state_initializes_empty_when_no_db_records(self):
        """When no HealthCheck records exist, state is empty."""
        from monitoring.services.state import StateTracker
        
        tracker = StateTracker()
        tracker.initialize()
        
        assert tracker.get_state("test-service") is None

    def test_state_initializes_from_db(self):
        """Loads last known state from HealthCheck table."""
        from monitoring.services.state import StateTracker
        
        # Create some health check records
        HealthCheckFactory(service_name="service-a", status="up")
        HealthCheckFactory(service_name="service-b", status="down")
        
        tracker = StateTracker()
        tracker.initialize()
        
        assert tracker.get_state("service-a") == "up"
        assert tracker.get_state("service-b") == "down"

    def test_state_initializes_with_latest_record(self):
        """When multiple records exist, uses the most recent."""
        from monitoring.services.state import StateTracker
        
        now = timezone.now()
        # Older record says up
        HealthCheckFactory(
            service_name="test-service",
            status="up",
            checked_at=now - timezone.timedelta(hours=1),
        )
        # Newer record says down
        HealthCheckFactory(
            service_name="test-service",
            status="down",
            checked_at=now,
        )
        
        tracker = StateTracker()
        tracker.initialize()
        
        assert tracker.get_state("test-service") == "down"


class TestStateTransitions:
    """Tests for detecting state transitions."""

    def test_state_detects_up_to_down(self):
        """Returns transition type 'went_down' when going from up to down."""
        from monitoring.services.state import StateTracker
        from monitoring.services.checker import HealthCheckResult
        
        HealthCheckFactory(service_name="test-service", status="up")
        
        tracker = StateTracker()
        tracker.initialize()
        
        result = HealthCheckResult(
            service_name="test-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Service Unavailable",
        )
        
        transition = tracker.update_and_get_transition(result)
        
        assert transition == "went_down"
        assert tracker.get_state("test-service") == "down"

    def test_state_detects_down_to_up(self):
        """Returns transition type 'recovered' when going from down to up."""
        from monitoring.services.state import StateTracker
        from monitoring.services.checker import HealthCheckResult
        
        HealthCheckFactory(service_name="test-service", status="down")
        
        tracker = StateTracker()
        tracker.initialize()
        
        result = HealthCheckResult(
            service_name="test-service",
            status="up",
            response_time_ms=150,
            status_code=200,
            error_message="",
        )
        
        transition = tracker.update_and_get_transition(result)
        
        assert transition == "recovered"
        assert tracker.get_state("test-service") == "up"

    def test_state_no_transition_when_stable_up(self):
        """Returns None when state hasn't changed (still up)."""
        from monitoring.services.state import StateTracker
        from monitoring.services.checker import HealthCheckResult
        
        HealthCheckFactory(service_name="test-service", status="up")
        
        tracker = StateTracker()
        tracker.initialize()
        
        result = HealthCheckResult(
            service_name="test-service",
            status="up",
            response_time_ms=150,
            status_code=200,
            error_message="",
        )
        
        transition = tracker.update_and_get_transition(result)
        
        assert transition is None
        assert tracker.get_state("test-service") == "up"

    def test_state_no_transition_when_stable_down(self):
        """Returns None when state hasn't changed (still down)."""
        from monitoring.services.state import StateTracker
        from monitoring.services.checker import HealthCheckResult
        
        HealthCheckFactory(service_name="test-service", status="down")
        
        tracker = StateTracker()
        tracker.initialize()
        
        result = HealthCheckResult(
            service_name="test-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Still down",
        )
        
        transition = tracker.update_and_get_transition(result)
        
        assert transition is None

    def test_first_check_for_new_service_no_transition(self):
        """First check for a new service returns None (no previous state)."""
        from monitoring.services.state import StateTracker
        from monitoring.services.checker import HealthCheckResult
        
        tracker = StateTracker()
        tracker.initialize()
        
        result = HealthCheckResult(
            service_name="brand-new-service",
            status="up",
            response_time_ms=150,
            status_code=200,
            error_message="",
        )
        
        transition = tracker.update_and_get_transition(result)
        
        # No transition on first check
        assert transition is None
        # But state is now tracked
        assert tracker.get_state("brand-new-service") == "up"

    def test_first_check_down_no_transition(self):
        """First check showing down returns None (would be noisy to alert immediately)."""
        from monitoring.services.state import StateTracker
        from monitoring.services.checker import HealthCheckResult
        
        tracker = StateTracker()
        tracker.initialize()
        
        result = HealthCheckResult(
            service_name="brand-new-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Error",
        )
        
        transition = tracker.update_and_get_transition(result)
        
        assert transition is None
        assert tracker.get_state("brand-new-service") == "down"
