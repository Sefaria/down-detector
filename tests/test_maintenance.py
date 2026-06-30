"""
Tests for scheduled maintenance: model helpers, page status override, and
Slack alert suppression during an active window.
"""
import pytest
from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from monitoring.models import Maintenance, HealthCheck
from tests.factories import HealthCheckFactory


pytestmark = pytest.mark.django_db


def _window(service="", offset_start_h=-1, offset_end_h=1, active=True, **kw):
    now = timezone.now()
    return Maintenance.objects.create(
        title=kw.get("title", "Planned work"),
        description=kw.get("description", ""),
        affected_services=service,
        start_time=now + timedelta(hours=offset_start_h),
        end_time=now + timedelta(hours=offset_end_h),
        active=active,
    )


class TestMaintenanceModel:
    def test_in_progress_window_covers_named_service(self):
        _window(service="MCP Server")
        covered = Maintenance.services_under_maintenance()
        assert covered == {"MCP Server"}

    def test_blank_window_covers_all_services(self, settings):
        _window(service="")
        covered = Maintenance.services_under_maintenance()
        assert covered == {s["name"] for s in settings.MONITORED_SERVICES}

    def test_future_window_is_not_in_progress(self):
        _window(service="Linker", offset_start_h=2, offset_end_h=4)
        assert Maintenance.services_under_maintenance() == set()

    def test_cancelled_window_is_ignored(self):
        _window(service="Linker", active=False)
        assert Maintenance.services_under_maintenance() == set()

    def test_current_and_upcoming_excludes_past(self):
        _window(title="past", offset_start_h=-5, offset_end_h=-3)
        _window(title="now", offset_start_h=-1, offset_end_h=1)
        _window(title="future", offset_start_h=2, offset_end_h=3)
        titles = [m.title for m in Maintenance.current_and_upcoming()]
        assert "past" not in titles
        assert set(titles) == {"now", "future"}


class TestMaintenanceValidation:
    """Model-level validation guards operator footguns (enforced in admin)."""

    def test_end_before_start_is_rejected(self):
        from django.core.exceptions import ValidationError

        now = timezone.now()
        m = Maintenance(
            title="bad window",
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=1),
        )
        with pytest.raises(ValidationError) as exc:
            m.full_clean()
        assert "end_time" in exc.value.message_dict

    def test_unknown_service_name_is_rejected_with_valid_list(self, settings):
        from django.core.exceptions import ValidationError

        now = timezone.now()
        m = Maintenance(
            title="typo",
            affected_services="not-a-real-service",
            start_time=now,
            end_time=now + timedelta(hours=1),
        )
        with pytest.raises(ValidationError) as exc:
            m.full_clean()
        msg = exc.value.message_dict["affected_services"][0]
        # The error names the valid services so the operator can fix the typo.
        assert settings.MONITORED_SERVICES[0]["name"] in msg

    def test_known_service_name_passes(self, settings):
        now = timezone.now()
        m = Maintenance(
            title="ok",
            affected_services=settings.MONITORED_SERVICES[0]["name"],
            start_time=now,
            end_time=now + timedelta(hours=1),
        )
        m.full_clean()  # should not raise

    def test_blank_scope_passes(self):
        now = timezone.now()
        Maintenance(
            title="all services",
            affected_services="",
            start_time=now,
            end_time=now + timedelta(hours=1),
        ).full_clean()  # should not raise


class TestMaintenanceOnStatusPage:
    def test_service_shows_under_maintenance(self, client, settings):
        name = settings.MONITORED_SERVICES[0]["name"]
        # Even a confirmed-down service shows maintenance during a window.
        for _ in range(settings.MONITORED_SERVICES[0].get("failure_threshold", 2)):
            HealthCheckFactory(service_name=name, status="down")
        _window(service=name)

        data = client.get(reverse("monitoring:status_api")).json()
        svc = next(s for s in data["services"] if s["name"] == name)
        assert svc["status"] == "maintenance"

    def test_maintenance_window_renders_in_section(self, client):
        _window(service="Linker", title="DB upgrade")
        content = client.get(reverse("monitoring:status")).content.decode()
        assert "Scheduled Maintenance" in content
        assert "DB upgrade" in content


class TestMaintenanceAlertSuppression:
    @patch("monitoring.services.scheduler.process_transitions_with_alerts")
    @patch("monitoring.services.scheduler.check_all_services")
    def test_alert_suppressed_for_service_under_maintenance(
        self, mock_check, mock_alerts, settings
    ):
        from monitoring.services.checker import HealthCheckResult
        from monitoring.services import scheduler
        from monitoring.services.state import reset_state_tracker

        name = settings.MONITORED_SERVICES[0]["name"]
        threshold = settings.MONITORED_SERVICES[0].get("failure_threshold", 2)

        # Seed prior state as up so a fresh down streak is a real transition.
        HealthCheckFactory(service_name=name, status="up")
        reset_state_tracker()
        _window(service=name)

        def down_result():
            return [HealthCheckResult(
                service_name=name, status="down", response_time_ms=None,
                status_code=503, error_message="down",
            )]

        mock_check.side_effect = lambda persist=True: down_result()

        # Run enough cycles to cross the threshold and produce a went_down.
        for _ in range(threshold + 1):
            scheduler.run_health_check_cycle()

        # A transition occurred, but it was suppressed by maintenance.
        mock_alerts.assert_not_called()
        reset_state_tracker()
