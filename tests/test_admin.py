"""
Tests for the Django admin registration of monitoring models.
"""
import pytest
from datetime import timedelta
from unittest.mock import MagicMock

from django.contrib import admin
from django.utils import timezone

from monitoring.admin import OutageAdmin
from monitoring.models import HealthCheck, Outage, Message


pytestmark = pytest.mark.django_db


def _outage_admin() -> OutageAdmin:
    return admin.site._registry[Outage]


class TestOutageAdminRegistration:
    """The Outage admin is registered and locked down to read-only fields."""

    def test_outage_is_registered(self):
        assert Outage in admin.site._registry

    def test_no_manual_creation(self):
        assert _outage_admin().has_add_permission(request=None) is False

    def test_all_fields_are_read_only(self):
        """Operators can't hand-edit outages; only the action mutates them."""
        ro = set(_outage_admin().readonly_fields)
        for field in ("service_name", "start_time", "end_time", "resolved",
                      "created_at", "updated_at"):
            assert field in ro

    def test_duration_display_formats_hours(self):
        outage = Outage(
            service_name="sefaria.org",
            start_time=timezone.now() - timedelta(hours=2, minutes=15),
            end_time=timezone.now(),
            resolved=True,
        )
        assert OutageAdmin(Outage, admin.site).duration_display(outage) == "2h 15m"

    def test_duration_display_formats_seconds(self):
        outage = Outage(
            service_name="sefaria.org",
            start_time=timezone.now() - timedelta(seconds=30),
            end_time=timezone.now(),
            resolved=True,
        )
        assert OutageAdmin(Outage, admin.site).duration_display(outage) == "30s"


class TestResolveOutagesAction:
    """Tests for the force-resolve admin action."""

    def _run_action(self, queryset):
        outage_admin = OutageAdmin(Outage, admin.site)
        request = MagicMock()
        request.user = "operator"
        outage_admin.message_user = MagicMock()
        outage_admin.resolve_outages(request, queryset)
        return outage_admin

    def test_resolves_open_outage_and_sets_end_time(self):
        outage = Outage.objects.create(
            service_name="sefaria.org",
            start_time=timezone.now() - timedelta(minutes=10),
        )
        assert outage.resolved is False and outage.end_time is None

        self._run_action(Outage.objects.filter(pk=outage.pk))

        outage.refresh_from_db()
        assert outage.resolved is True
        assert outage.end_time is not None

    def test_preserves_existing_end_time(self):
        end = timezone.now() - timedelta(minutes=1)
        outage = Outage.objects.create(
            service_name="sefaria.org",
            start_time=timezone.now() - timedelta(minutes=10),
            end_time=end,
        )

        self._run_action(Outage.objects.filter(pk=outage.pk))

        outage.refresh_from_db()
        assert outage.resolved is True
        assert outage.end_time == end

    def test_skips_already_resolved(self):
        outage = Outage.objects.create(
            service_name="sefaria.org",
            start_time=timezone.now() - timedelta(minutes=10),
            end_time=timezone.now(),
            resolved=True,
        )
        admin_obj = self._run_action(Outage.objects.filter(pk=outage.pk))
        # Nothing to resolve -> the "nothing to resolve" message is shown.
        admin_obj.message_user.assert_called_once()
        assert "nothing" in admin_obj.message_user.call_args.args[1].lower()


class TestRegisteredModels:
    """The monitoring models operators rely on are all registered."""

    def test_core_models_registered(self):
        for model in (HealthCheck, Outage, Message):
            assert model in admin.site._registry
