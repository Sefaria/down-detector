"""
Tests for the Django admin registration of monitoring models.
"""
import pytest
from datetime import timedelta

from django.contrib import admin
from django.utils import timezone

from monitoring.admin import OutageAdmin
from monitoring.models import HealthCheck, Outage, Message


pytestmark = pytest.mark.django_db


class TestOutageAdmin:
    """Tests for the read-only Outage admin."""

    def test_outage_is_registered(self):
        """Outage is exposed in the admin so operators can inspect it."""
        assert Outage in admin.site._registry

    def test_outage_admin_is_read_only(self):
        """Outages are system-managed: no manual add or edit."""
        outage_admin = admin.site._registry[Outage]
        assert outage_admin.has_add_permission(request=None) is False
        assert outage_admin.has_change_permission(request=None) is False

    def test_duration_display_formats_hours(self):
        """A multi-hour outage renders as 'Xh Ym'."""
        outage = Outage(
            service_name="sefaria.org",
            start_time=timezone.now() - timedelta(hours=2, minutes=15),
            end_time=timezone.now(),
            resolved=True,
        )
        assert OutageAdmin(Outage, admin.site).duration_display(outage) == "2h 15m"

    def test_duration_display_formats_seconds(self):
        """A sub-minute outage renders in seconds."""
        outage = Outage(
            service_name="sefaria.org",
            start_time=timezone.now() - timedelta(seconds=30),
            end_time=timezone.now(),
            resolved=True,
        )
        assert OutageAdmin(Outage, admin.site).duration_display(outage) == "30s"


class TestRegisteredModels:
    """The monitoring models operators rely on are all registered."""

    def test_core_models_registered(self):
        for model in (HealthCheck, Outage, Message):
            assert model in admin.site._registry
