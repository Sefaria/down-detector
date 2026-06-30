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
        """When a service is confirmed down (threshold met), shows outage."""
        service_name = settings.MONITORED_SERVICES[0]["name"]
        threshold = settings.MONITORED_SERVICES[0].get("failure_threshold", 2)

        # Create enough consecutive failures to meet the threshold
        for _ in range(threshold):
            HealthCheckFactory(service_name=service_name, status="down")

        response = client.get(reverse("monitoring:status"))
        content = response.content.decode()

        # Either "Major Outage" or "Outage" should appear
        assert "Outage" in content

    def test_single_failure_below_threshold_shows_operational(self, client, settings):
        """A single failure below the threshold still shows operational."""
        service_name = settings.MONITORED_SERVICES[0]["name"]

        # Create one up then one down — below threshold of 2
        HealthCheckFactory(service_name=service_name, status="up")
        HealthCheckFactory(service_name=service_name, status="down")

        response = client.get(reverse("monitoring:status"))
        content = response.content.decode()

        assert "All Systems Operational" in content


class TestStatusApi:
    """Tests for the JSON polling endpoint used by the live page."""

    def test_api_returns_json_snapshot(self, client, settings):
        """The endpoint returns overall status + per-service status as JSON."""
        for cfg in settings.MONITORED_SERVICES:
            HealthCheckFactory(service_name=cfg["name"], status="up", response_time_ms=123)

        response = client.get(reverse("monitoring:status_api"))

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        data = response.json()
        assert data["overall_status"] == "operational"
        assert data["status_label"] == "All Systems Operational"
        names = {s["name"] for s in data["services"]}
        assert names == {c["name"] for c in settings.MONITORED_SERVICES}
        assert all("response_time_ms" in s and "detail" in s for s in data["services"])

    def test_api_reflects_confirmed_outage(self, client, settings):
        """A confirmed-down service is reported down by the API."""
        cfg = settings.MONITORED_SERVICES[0]
        threshold = cfg.get("failure_threshold", 2)
        for _ in range(threshold):
            HealthCheckFactory(service_name=cfg["name"], status="down", status_code=503)

        data = client.get(reverse("monitoring:status_api")).json()

        assert data["overall_status"] == "major"
        svc = next(s for s in data["services"] if s["name"] == cfg["name"])
        assert svc["status"] == "down"

    def test_api_does_not_leak_raw_error(self, client, settings):
        """The JSON 'detail' is sanitized, never the raw internal error."""
        cfg = settings.MONITORED_SERVICES[0]
        threshold = cfg.get("failure_threshold", 2)
        for _ in range(threshold):
            HealthCheckFactory(
                service_name=cfg["name"],
                status="down",
                status_code=None,
                error_message='connection to server at "10.0.3.3" failed',
            )

        svc = next(
            s for s in client.get(reverse("monitoring:status_api")).json()["services"]
            if s["name"] == cfg["name"]
        )
        assert "10.0.3.3" not in svc["detail"]
        assert svc["detail"] == "Service unreachable"


class TestDegradedState:
    """A slow-but-up service is shown as degraded (page-only, no Slack)."""

    def test_slow_service_is_degraded(self, client, settings):
        service_name = settings.MONITORED_SERVICES[0]["name"]
        threshold = settings.MONITORED_SERVICES[0].get("failure_threshold", 2)
        slow = settings.DEGRADED_RESPONSE_MS + 1000

        for _ in range(threshold):
            HealthCheckFactory(
                service_name=service_name, status="up", response_time_ms=slow
            )

        data = client.get(reverse("monitoring:status_api")).json()
        svc = next(s for s in data["services"] if s["name"] == service_name)

        assert svc["status"] == "degraded"
        assert data["overall_status"] == "degraded"
        assert data["status_label"] == "Degraded Performance"

    def test_fast_service_is_operational(self, client, settings):
        service_name = settings.MONITORED_SERVICES[0]["name"]
        threshold = settings.MONITORED_SERVICES[0].get("failure_threshold", 2)

        for _ in range(threshold):
            HealthCheckFactory(
                service_name=service_name, status="up", response_time_ms=120
            )

        svc = next(
            s for s in client.get(reverse("monitoring:status_api")).json()["services"]
            if s["name"] == service_name
        )
        assert svc["status"] == "up"

    def test_overall_partial_when_some_down(self, client, settings):
        """Some-but-not-all services down => partial; all down => major."""
        from monitoring.views import get_overall_status

        statuses_some = [{"status": "down"}, {"status": "up"}]
        assert get_overall_status(statuses_some, []) == "partial"

        statuses_all = [{"status": "down"}, {"status": "down"}]
        assert get_overall_status(statuses_all, []) == "major"


class TestPublicErrorSanitization:
    """The public page must never echo raw internal error detail."""

    def test_internal_host_is_not_leaked(self, client, settings):
        """A down service's raw error (with an internal IP) is sanitized."""
        service_name = settings.MONITORED_SERVICES[0]["name"]
        threshold = settings.MONITORED_SERVICES[0].get("failure_threshold", 2)

        raw = 'connection to server at "10.0.3.3", port 5432 failed: FATAL: sorry'
        for _ in range(threshold):
            HealthCheckFactory(
                service_name=service_name,
                status="down",
                status_code=None,
                error_message=raw,
            )

        content = client.get(reverse("monitoring:status")).content.decode()

        assert "10.0.3.3" not in content
        assert "5432" not in content
        assert "Service unreachable" in content

    def test_status_code_is_surfaced_but_not_raw_text(self, client, settings):
        """An HTTP-code failure shows the code, not the raw 'Expected ...' text."""
        service_name = settings.MONITORED_SERVICES[0]["name"]
        threshold = settings.MONITORED_SERVICES[0].get("failure_threshold", 2)

        for _ in range(threshold):
            HealthCheckFactory(
                service_name=service_name,
                status="down",
                status_code=521,
                error_message="Expected 200, got 521",
            )

        content = client.get(reverse("monitoring:status")).content.decode()

        assert "Unexpected response (HTTP 521)" in content
        assert "Expected 200, got 521" not in content

    def test_detail_helper_classifies_errors(self):
        """Unit-level checks for the sanitizer's mapping."""
        from monitoring.views import get_public_status_detail

        assert get_public_status_detail("", None) == ""
        assert get_public_status_detail("Request timed out: x", None) == "Request timed out"
        assert (
            get_public_status_detail('connection to server at "10.0.3.3"', None)
            == "Service unreachable"
        )
        assert (
            get_public_status_detail("Expected 200, got 503", 503)
            == "Unexpected response (HTTP 503)"
        )
