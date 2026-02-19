"""
State transition tracker for detecting service status changes.

This module tracks the state of monitored services and detects
transitions between UP and DOWN states, which trigger Slack alerts.

A configurable per-service failure threshold prevents noisy alerts
from brief blips — a service must fail N consecutive check cycles
before being reported as down.
"""
import logging
from typing import Literal

from django.conf import settings
from django.db.models import Max

from monitoring.models import HealthCheck
from monitoring.services.checker import HealthCheckResult

logger = logging.getLogger(__name__)

# Type alias for transition types
TransitionType = Literal["went_down", "recovered"] | None


class StateTracker:
    """
    Tracks service states and detects transitions.

    State transitions are used to trigger Slack alerts only when
    a service changes state (UP -> DOWN or DOWN -> UP), preventing
    alert storms during extended outages.

    A per-service failure threshold requires N consecutive failed
    check cycles before a DOWN alert is sent. This filters brief
    blips that self-resolve within a few minutes.
    """

    def __init__(self):
        """Initialize with empty state."""
        self._states: dict[str, str] = {}
        self._failure_counts: dict[str, int] = {}
        self._confirmed_down: set[str] = set()
        self._initialized: bool = False

    def _get_threshold(self, service_name: str) -> int:
        """
        Get the failure threshold for a service.

        Looks up ``failure_threshold`` in the service's config entry
        in ``MONITORED_SERVICES``, falling back to the global
        ``ALERT_AFTER_CONSECUTIVE_FAILURES`` setting (default 1).
        """
        services = getattr(settings, "MONITORED_SERVICES", [])
        for svc in services:
            if svc.get("name") == service_name:
                return svc.get(
                    "failure_threshold",
                    getattr(settings, "ALERT_AFTER_CONSECUTIVE_FAILURES", 1),
                )
        return getattr(settings, "ALERT_AFTER_CONSECUTIVE_FAILURES", 1)

    def initialize(self) -> None:
        """
        Load initial state from database.

        Queries the latest HealthCheck record for each service
        to populate the initial state.
        """
        # Get the latest checked_at for each service
        latest_checks = (
            HealthCheck.objects
            .values("service_name")
            .annotate(latest_checked_at=Max("checked_at"))
        )

        # Fetch the actual records for those timestamps
        for entry in latest_checks:
            service_name = entry["service_name"]
            latest_checked_at = entry["latest_checked_at"]

            health_check = HealthCheck.objects.filter(
                service_name=service_name,
                checked_at=latest_checked_at,
            ).first()

            if health_check:
                self._states[service_name] = health_check.status
                if health_check.status == "down":
                    # Already confirmed down from previous run
                    self._confirmed_down.add(service_name)
                    self._failure_counts[service_name] = self._get_threshold(
                        service_name
                    )
                else:
                    self._failure_counts[service_name] = 0
                logger.debug(
                    f"Initialized state for {service_name}: {health_check.status}"
                )

        self._initialized = True
        logger.info(f"StateTracker initialized with {len(self._states)} services")

    def get_state(self, service_name: str) -> str | None:
        """
        Get the current confirmed state of a service.

        Args:
            service_name: Name of the service

        Returns:
            "up" or "down" if known, None if service not tracked yet
        """
        return self._states.get(service_name)

    def update_and_get_transition(
        self, result: HealthCheckResult
    ) -> TransitionType:
        """
        Update state and return the transition type if any.

        A DOWN transition requires N consecutive failures (per the
        service's ``failure_threshold``).  A recovery fires immediately
        on the first successful check.

        Args:
            result: The health check result to process

        Returns:
            "went_down" if service confirmed down after threshold failures
            "recovered" if service went from confirmed-down to up
            None if no reportable transition
        """
        service_name = result.service_name
        new_status = result.status
        old_status = self._states.get(service_name)

        # First time seeing this service — no transition
        if old_status is None:
            self._states[service_name] = new_status
            self._failure_counts[service_name] = 0
            if new_status == "down":
                # Start counting but don't alert on first ever check
                self._failure_counts[service_name] = 1
            logger.info(f"First check for {service_name}: {new_status}")
            return None

        if new_status == "down":
            self._failure_counts[service_name] = (
                self._failure_counts.get(service_name, 0) + 1
            )
            count = self._failure_counts[service_name]
            threshold = self._get_threshold(service_name)

            if count >= threshold and service_name not in self._confirmed_down:
                self._confirmed_down.add(service_name)
                self._states[service_name] = "down"
                logger.warning(
                    f"Service {service_name} went DOWN "
                    f"(confirmed after {count} consecutive failures)"
                )
                return "went_down"

            # Not enough failures yet, or already confirmed down
            logger.debug(
                f"Service {service_name} check failed "
                f"({count}/{threshold} consecutive failures)"
            )
            return None

        # new_status == "up"
        self._failure_counts[service_name] = 0

        if service_name in self._confirmed_down:
            self._confirmed_down.discard(service_name)
            self._states[service_name] = "up"
            logger.info(f"Service {service_name} RECOVERED")
            return "recovered"

        # Was not confirmed down — blip resolved silently
        self._states[service_name] = "up"
        return None

    def process_results(
        self, results: list[HealthCheckResult]
    ) -> list[tuple[HealthCheckResult, TransitionType]]:
        """
        Process multiple health check results.

        Args:
            results: List of health check results

        Returns:
            List of (result, transition) tuples for results with transitions
        """
        transitions = []
        for result in results:
            transition = self.update_and_get_transition(result)
            if transition is not None:
                transitions.append((result, transition))
        return transitions


# Global singleton instance
_tracker: StateTracker | None = None


def get_state_tracker() -> StateTracker:
    """
    Get the global StateTracker instance.

    Creates and initializes the tracker on first call.
    """
    global _tracker
    if _tracker is None:
        _tracker = StateTracker()
        _tracker.initialize()
    return _tracker


def reset_state_tracker() -> None:
    """Reset the global state tracker (useful for testing)."""
    global _tracker
    _tracker = None
