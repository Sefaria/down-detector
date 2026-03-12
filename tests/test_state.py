"""
Tests for the state transition tracker.
"""
import pytest
from unittest.mock import patch
from django.utils import timezone

from monitoring.services.checker import HealthCheckResult
from tests.factories import HealthCheckFactory


pytestmark = pytest.mark.django_db


def _make_result(service_name: str, status: str, **kwargs) -> HealthCheckResult:
    """Helper to build a HealthCheckResult."""
    defaults = {
        "service_name": service_name,
        "status": status,
        "response_time_ms": 150 if status == "up" else None,
        "status_code": 200 if status == "up" else 503,
        "error_message": "" if status == "up" else "Error",
    }
    defaults.update(kwargs)
    return HealthCheckResult(**defaults)


# ---------------------------------------------------------------------------
# Helpers to configure per-service thresholds via settings override
# ---------------------------------------------------------------------------

THRESHOLD_2_SERVICES = [
    {"name": "test-service", "failure_threshold": 2},
]

THRESHOLD_3_SERVICES = [
    {"name": "linker-service", "failure_threshold": 3},
]


class TestStateTrackerInitialization:
    """Tests for StateTracker initialization."""

    def test_state_initializes_empty_when_no_db_records(self):
        """When no HealthCheck records exist, state is empty."""
        from monitoring.services.state import StateTracker

        tracker = StateTracker()
        tracker.initialize()

        assert tracker.get_state("test-service") is None

    def test_state_initializes_from_db(self):
        """Loads last known state from HealthCheck table."""
        from monitoring.services.state import StateTracker

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
        HealthCheckFactory(
            service_name="test-service",
            status="up",
            checked_at=now - timezone.timedelta(hours=1),
        )
        HealthCheckFactory(
            service_name="test-service",
            status="down",
            checked_at=now,
        )

        tracker = StateTracker()
        tracker.initialize()

        assert tracker.get_state("test-service") == "down"

    def test_initialization_with_down_state_marks_confirmed(self):
        """Services loaded as 'down' from DB are marked confirmed."""
        from monitoring.services.state import StateTracker

        HealthCheckFactory(service_name="test-service", status="down")

        tracker = StateTracker()
        tracker.initialize()

        # Should be confirmed down — a recovery should trigger an alert
        result = _make_result("test-service", "up")
        transition, outage_start = tracker.update_and_get_transition(result)
        assert transition == "recovered"


class TestStateTransitionsWithThreshold:
    """Tests for consecutive failure threshold logic."""

    @patch(
        "monitoring.services.state.settings",
    )
    def test_single_failure_no_alert_with_threshold_2(self, mock_settings):
        """One failure is not enough to trigger an alert when threshold is 2."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = THRESHOLD_2_SERVICES
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 2

        HealthCheckFactory(service_name="test-service", status="up")

        tracker = StateTracker()
        tracker.initialize()

        result = _make_result("test-service", "down")
        transition, outage_start = tracker.update_and_get_transition(result)

        assert transition is None
        # Internal state should still be "up" (not confirmed down)
        assert tracker.get_state("test-service") == "up"

    @patch("monitoring.services.state.settings")
    def test_two_consecutive_failures_trigger_alert_with_threshold_2(
        self, mock_settings
    ):
        """Two consecutive failures trigger went_down when threshold is 2."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = THRESHOLD_2_SERVICES
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 2

        HealthCheckFactory(service_name="test-service", status="up")

        tracker = StateTracker()
        tracker.initialize()

        # First failure — no alert
        result1 = _make_result("test-service", "down")
        transition1, start1 = tracker.update_and_get_transition(result1)
        assert transition1 is None

        # Second failure — alert!
        result2 = _make_result("test-service", "down")
        transition2, start2 = tracker.update_and_get_transition(result2)
        assert transition2 == "went_down"
        assert tracker.get_state("test-service") == "down"

    @patch("monitoring.services.state.settings")
    def test_three_consecutive_failures_trigger_alert_with_threshold_3(
        self, mock_settings
    ):
        """Three consecutive failures trigger went_down when threshold is 3."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = THRESHOLD_3_SERVICES
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 2

        HealthCheckFactory(service_name="linker-service", status="up")

        tracker = StateTracker()
        tracker.initialize()

        # Failures 1 and 2 — no alert
        for _ in range(2):
            result = _make_result("linker-service", "down")
            t, _ = tracker.update_and_get_transition(result)
            assert t is None

        # Failure 3 — alert!
        result3 = _make_result("linker-service", "down")
        t, _ = tracker.update_and_get_transition(result3)
        assert t == "went_down"

    @patch("monitoring.services.state.settings")
    def test_success_resets_failure_counter(self, mock_settings):
        """A success between failures resets the counter — no alert."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = THRESHOLD_2_SERVICES
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 2

        HealthCheckFactory(service_name="test-service", status="up")

        tracker = StateTracker()
        tracker.initialize()

        # Fail once
        t, _ = tracker.update_and_get_transition(
            _make_result("test-service", "down")
        )
        assert t is None

        # Succeed (resets counter)
        t, _ = tracker.update_and_get_transition(
            _make_result("test-service", "up")
        )
        assert t is None

        # Fail once again — counter is back to 1, still below threshold
        t, _ = tracker.update_and_get_transition(
            _make_result("test-service", "down")
        )
        assert t is None

    @patch("monitoring.services.state.settings")
    def test_recovery_fires_immediately_after_confirmed_down(self, mock_settings):
        """Recovery alert fires on the first success after confirmed down."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = THRESHOLD_2_SERVICES
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 2

        HealthCheckFactory(service_name="test-service", status="up")

        tracker = StateTracker()
        tracker.initialize()

        # Confirm down (2 failures)
        tracker.update_and_get_transition(_make_result("test-service", "down"))
        tracker.update_and_get_transition(_make_result("test-service", "down"))

        # First success — immediate recovery
        transition, outage_start = tracker.update_and_get_transition(
            _make_result("test-service", "up")
        )
        assert transition == "recovered"
        assert tracker.get_state("test-service") == "up"

    @patch("monitoring.services.state.settings")
    def test_blip_resolves_silently(self, mock_settings):
        """A single fail followed by success generates zero alerts."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = THRESHOLD_2_SERVICES
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 2

        HealthCheckFactory(service_name="test-service", status="up")

        tracker = StateTracker()
        tracker.initialize()

        # Fail once
        t1, _ = tracker.update_and_get_transition(
            _make_result("test-service", "down")
        )
        # Recover
        t2, _ = tracker.update_and_get_transition(
            _make_result("test-service", "up")
        )

        assert t1 is None
        assert t2 is None  # No recovery alert — was never confirmed down

    @patch("monitoring.services.state.settings")
    def test_continued_down_after_confirmation_no_extra_alerts(self, mock_settings):
        """After confirmed down, additional failures produce no more alerts."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = THRESHOLD_2_SERVICES
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 2

        HealthCheckFactory(service_name="test-service", status="up")

        tracker = StateTracker()
        tracker.initialize()

        # Confirm down
        tracker.update_and_get_transition(_make_result("test-service", "down"))
        t, _ = tracker.update_and_get_transition(_make_result("test-service", "down"))
        assert t == "went_down"

        # Further failures — no extra alerts
        t, _ = tracker.update_and_get_transition(_make_result("test-service", "down"))
        assert t is None
        
        t, _ = tracker.update_and_get_transition(_make_result("test-service", "down"))
        assert t is None

    def test_first_check_for_new_service_no_transition(self):
        """First check for a new service returns None (no previous state)."""
        from monitoring.services.state import StateTracker

        tracker = StateTracker()
        tracker.initialize()

        result = _make_result("brand-new-service", "up")
        transition, _ = tracker.update_and_get_transition(result)

        assert transition is None
        assert tracker.get_state("brand-new-service") == "up"

    def test_first_check_down_no_transition(self):
        """First check showing down returns None (would be noisy to alert)."""
        from monitoring.services.state import StateTracker

        tracker = StateTracker()
        tracker.initialize()

        result = _make_result("brand-new-service", "down")
        transition, _ = tracker.update_and_get_transition(result)

        assert transition is None
        assert tracker.get_state("brand-new-service") == "down"


class TestProcessResults:
    """Tests for batch processing of results."""

    @patch("monitoring.services.state.settings")
    def test_process_results_returns_only_transitions(self, mock_settings):
        """process_results filters out non-transition results."""
        from monitoring.services.state import StateTracker

        mock_settings.MONITORED_SERVICES = [
            {"name": "svc-a", "failure_threshold": 1},
            {"name": "svc-b", "failure_threshold": 1},
        ]
        mock_settings.ALERT_AFTER_CONSECUTIVE_FAILURES = 1

        HealthCheckFactory(service_name="svc-a", status="up")
        HealthCheckFactory(service_name="svc-b", status="down")

        tracker = StateTracker()
        tracker.initialize()

        results = [
            _make_result("svc-a", "down"),   # up -> down with threshold 1
            _make_result("svc-b", "up"),      # down -> up (recovery)
        ]

        transitions = tracker.process_results(results)
        assert len(transitions) == 2
        assert transitions[0][1] == "went_down"
        assert transitions[1][1] == "recovered"
