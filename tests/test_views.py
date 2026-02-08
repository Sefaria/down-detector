"""
Tests for the status page view.
"""
import pytest
from django.urls import reverse

from tests.factories import HealthCheckFactory, MessageFactory


pytestmark = pytest.mark.django_db


class TestStatusPageView:
    """Tests for the status page view."""

    def test_status_page_returns_200(self, client):
        """GET /status/ returns 200."""
        response = client.get(reverse("monitoring:status"))
        assert response.status_code == 200

    def test_status_page_shows_service_name(self, client, settings):
        """Service names appear in response."""
        # Create health check for first configured service
        service_name = settings.MONITORED_SERVICES[0]["name"]
        HealthCheckFactory(service_name=service_name, status="up")
        
        response = client.get(reverse("monitoring:status"))
        content = response.content.decode()
        
        assert service_name in content

    def test_status_page_shows_active_incidents(self, client):
        """Active incidents appear on the status page."""
        MessageFactory(severity="high", text="Server is on fire", active=True)
        
        response = client.get(reverse("monitoring:status"))
        content = response.content.decode()
        
        assert "Server is on fire" in content

    def test_status_page_shows_resolved_incidents(self, client):
        """Resolved incidents appear in history section."""
        MessageFactory(severity="resolved", text="Fixed the issue", active=False)
        
        response = client.get(reverse("monitoring:status"))
        content = response.content.decode()
        
        assert "Fixed the issue" in content

    def test_status_page_uses_template(self, client):
        """Status page uses status.html template."""
        response = client.get(reverse("monitoring:status"))
        
        # Check template was used
        assert "monitoring/status.html" in [t.name for t in response.templates]


class TestStatusPageLogic:
    """Tests for overall status calculation logic."""

    def test_all_up_shows_operational(self, client, settings):
        """When all services are up, shows 'All Systems Operational'."""
        for service_config in settings.MONITORED_SERVICES:
            HealthCheckFactory(service_name=service_config["name"], status="up")
        
        response = client.get(reverse("monitoring:status"))
        content = response.content.decode()
        
        assert "All Systems Operational" in content

    def test_service_down_shows_outage(self, client, settings):
        """When a service is down, shows outage status."""
        # Create one service down
        HealthCheckFactory(
            service_name=settings.MONITORED_SERVICES[0]["name"],
            status="down",
        )
        
        response = client.get(reverse("monitoring:status"))
        content = response.content.decode()
        
        # Either "Major Outage" or "Outage" should appear
        assert "Outage" in content
