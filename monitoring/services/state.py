"""
State transition tracker for detecting service status changes.

This module tracks the state of monitored services and detects
transitions between UP and DOWN states, which trigger Slack alerts.
"""
import logging
from typing import Literal

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
    """

    def __init__(self):
        """Initialize with empty state."""
        self._states: dict[str, str] = {}
        self._initialized: bool = False

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
                logger.debug(
                    f"Initialized state for {service_name}: {health_check.status}"
                )

        self._initialized = True
        logger.info(f"StateTracker initialized with {len(self._states)} services")

    def get_state(self, service_name: str) -> str | None:
        """
        Get the current state of a service.
        
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
        
        Args:
            result: The health check result to process
            
        Returns:
            "went_down" if service went from up to down
            "recovered" if service went from down to up
            None if no transition (stable state or first check)
        """
        service_name = result.service_name
        new_status = result.status
        old_status = self._states.get(service_name)

        # Update the tracked state
        self._states[service_name] = new_status

        # Determine transition type
        if old_status is None:
            # First time seeing this service - no transition
            logger.info(f"First check for {service_name}: {new_status}")
            return None

        if old_status == new_status:
            # No change
            return None

        if old_status == "up" and new_status == "down":
            logger.warning(f"Service {service_name} went DOWN")
            return "went_down"

        if old_status == "down" and new_status == "up":
            logger.info(f"Service {service_name} RECOVERED")
            return "recovered"

        # Shouldn't happen, but handle gracefully
        logger.error(
            f"Unexpected state transition for {service_name}: "
            f"{old_status} -> {new_status}"
        )
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
