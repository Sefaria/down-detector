"""
Tests for the admin enhancements: Operators group, landing dashboard, and the
checkbox-based maintenance scope.
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse


pytestmark = pytest.mark.django_db


class TestOperatorsGroup:
    def test_group_created_by_migration(self):
        assert Group.objects.filter(name="Operators").exists()

    def test_group_has_incident_and_maintenance_perms(self):
        codenames = set(
            Group.objects.get(name="Operators")
            .permissions.values_list("codename", flat=True)
        )
        # Can author incidents + maintenance and force-resolve outages.
        assert {"add_message", "change_message", "view_message"} <= codenames
        assert {"add_maintenance", "change_maintenance"} <= codenames
        assert "change_outage" in codenames  # for the force-resolve action

    def test_group_is_least_privilege(self):
        """No delete permissions and no user/group management."""
        codenames = set(
            Group.objects.get(name="Operators")
            .permissions.values_list("codename", flat=True)
        )
        assert not any(c.startswith("delete_") for c in codenames)
        assert not any("user" in c or "group" in c for c in codenames)


class TestAdminDashboard:
    def _superuser(self):
        U = get_user_model()
        return U.objects.create_superuser("dash", "d@x.com", "pw12345!")

    def test_index_shows_status_dashboard(self, client, settings):
        from tests.factories import HealthCheckFactory

        HealthCheckFactory(
            service_name=settings.MONITORED_SERVICES[0]["name"],
            status="up",
            response_time_ms=120,
        )
        client.force_login(self._superuser())

        content = client.get(reverse("admin:index")).content.decode()

        assert "System status" in content
        assert "Open outages" in content

    def test_maintenance_form_uses_checkbox_scope(self, client, settings):
        client.force_login(self._superuser())

        content = client.get(
            reverse("admin:monitoring_maintenance_add")
        ).content.decode()

        # Each configured service is offered as a checkbox option.
        assert 'type="checkbox"' in content
        assert settings.MONITORED_SERVICES[0]["name"] in content


class TestAxesConfigured:
    def test_axes_installed_and_backend_first(self, settings):
        assert "axes" in settings.INSTALLED_APPS
        assert settings.AUTHENTICATION_BACKENDS[0] == "axes.backends.AxesStandaloneBackend"
        assert settings.AXES_FAILURE_LIMIT >= 1
