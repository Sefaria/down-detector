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
from datetime import datetime

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from monitoring.models import HealthCheck, Outage
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
        self._outage_start_times: dict[str, datetime] = {}
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
                    
                    # Try to reconstruct when this outage started from the DB
                    last_up = HealthCheck.objects.filter(
                        service_name=service_name, status="up"
                    ).order_by("-checked_at").first()
                    
                    if last_up:
                        first_down = HealthCheck.objects.filter(
                            service_name=service_name,
                            status="down",
                            checked_at__gt=last_up.checked_at,
                        ).order_by("checked_at").first()
                        if first_down:
                            self._outage_start_times[service_name] = first_down.checked_at
                    else:
                        earliest_down = HealthCheck.objects.filter(
                            service_name=service_name, status="down"
                        ).order_by("checked_at").first()
                        if earliest_down:
                            self._outage_start_times[service_name] = earliest_down.checked_at
                            
                    # Fallback to the latest check if we really couldn't find anything
                    if service_name not in self._outage_start_times:
                        self._outage_start_times[service_name] = health_check.checked_at

                    # Ensure an unresolved Outage row exists for this
                    # confirmed-down service. This upholds the invariant
                    # "confirmed_down ⟺ an open Outage row" so that a later
                    # cycle can tell when an outage was resolved out-of-band
                    # (e.g. from the admin). It also gives the eventual
                    # recovery alert an accurate downtime after a restart.
                    start_time = self._outage_start_times[service_name]
                    if not Outage.objects.filter(
                        service_name=service_name, resolved=False
                    ).exists():
                        Outage.objects.create(
                            service_name=service_name, start_time=start_time
                        )
                else:
                    self._failure_counts[service_name] = 0
                logger.debug(
                    f"Initialized state for {service_name}: {health_check.status}"
                )

        self._initialized = True
        logger.info(f"StateTracker initialized with {len(self._states)} services")

    def _reconcile_external_resolution(self, service_name: str) -> None:
        """
        Detect and absorb an outage that was resolved outside this process.

        The scheduler holds outage state in memory, but an operator can
        force-close an outage from the Django admin, which runs in a
        separate web process and cannot touch this tracker's memory. When
        that happens the unresolved ``Outage`` row disappears while this
        tracker still believes the service is down.

        Relying on the invariant that a confirmed-down service always has
        an open ``Outage`` row (maintained by :meth:`initialize`,
        ``went_down`` and ``recovered``), the absence of that row means the
        outage was closed elsewhere. We then drop the stale in-memory
        "down" bookkeeping so this cycle's result is evaluated from a clean
        slate:

        - if the service has genuinely recovered, the next "up" check is
          treated as an already-resolved blip (no duplicate recovery alert);
        - if the service is still failing, the normal threshold logic opens
          a fresh ``Outage`` and re-alerts.

        This keeps the database the single source of truth for whether a
        service is currently in an outage, even across processes.
        """
        if service_name not in self._confirmed_down:
            return

        still_open = Outage.objects.filter(
            service_name=service_name, resolved=False
        ).exists()
        if still_open:
            return

        logger.info(
            f"Service {service_name}: outage resolved out-of-band "
            f"(no open Outage row); clearing stale in-memory down state"
        )
        self._confirmed_down.discard(service_name)
        self._states[service_name] = "up"
        self._failure_counts[service_name] = 0
        self._outage_start_times.pop(service_name, None)

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
    ) -> tuple[TransitionType, Outage | None]:
        """
        Update state and return the transition type and exact Outage record.

        A DOWN transition requires N consecutive failures (per the
        service's ``failure_threshold``).  A recovery fires immediately
        on the first successful check.

        Args:
            result: The health check result to process

        Returns:
            Tuple of (transition_type, Outage record):
            - "went_down" if service confirmed down after threshold failures (returns newly created Outage)
            - "recovered" if service went from confirmed-down to up (returns resolved Outage)
            - None if no reportable transition
            - Outage is returned for "went_down" and "recovered" transitions, or None.
        """
        service_name = result.service_name
        new_status = result.status

        # If this outage was closed out-of-band (e.g. an operator resolved
        # it from the admin), drop our stale in-memory "down" state before
        # evaluating this result.
        self._reconcile_external_resolution(service_name)

        old_status = self._states.get(service_name)

        # First time seeing this service — no transition
        if old_status is None:
            self._states[service_name] = new_status
            self._failure_counts[service_name] = 0
            if new_status == "down":
                # Start counting but don't alert on first ever check
                self._failure_counts[service_name] = 1
                self._outage_start_times[service_name] = timezone.now()
            logger.info(f"First check for {service_name}: {new_status}")
            return None, None

        if new_status == "down":
            if self._failure_counts.get(service_name, 0) == 0:
                # First failure in a potential new outage streak
                self._outage_start_times[service_name] = timezone.now()
                
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
                
                # Check if there's already an active outage to avoid duplicates
                outage = Outage.objects.filter(
                    service_name=service_name, resolved=False
                ).first()
                if not outage:
                    start_time = self._outage_start_times.get(
                        service_name, timezone.now()
                    )
                    outage = Outage.objects.create(
                        service_name=service_name, start_time=start_time
                    )
                    
                return "went_down", outage

            # Not enough failures yet, or already confirmed down
            logger.debug(
                f"Service {service_name} check failed "
                f"({count}/{threshold} consecutive failures)"
            )
            return None, None

        # new_status == "up"
        self._failure_counts[service_name] = 0

        if service_name in self._confirmed_down:
            self._confirmed_down.discard(service_name)
            self._states[service_name] = "up"
            logger.info(f"Service {service_name} RECOVERED")
            
            # Resolve the active outage
            outage = Outage.objects.filter(
                service_name=service_name, resolved=False
            ).first()
            if outage:
                outage.end_time = timezone.now()
                outage.resolved = True
                outage.save()
                
            return "recovered", outage

        # Was not confirmed down — blip resolved silently
        self._states[service_name] = "up"
        # Always clear outage start on up explicitly to prevent leaking state
        self._outage_start_times.pop(service_name, None)
        return None, None

    def process_results(
        self, results: list[HealthCheckResult]
    ) -> list[tuple[HealthCheckResult, TransitionType, Outage | None]]:
        """
        Process multiple health check results.

        Args:
            results: List of health check results

        Returns:
            List of (result, transition, Outage) tuples for results with transitions
        """
        transitions = []
        for result in results:
            transition, outage = self.update_and_get_transition(result)
            if transition is not None:
                transitions.append((result, transition, outage))
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
