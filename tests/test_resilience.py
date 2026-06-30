"""
Tests that the status page and API survive the monitor's own database being
down — a status page must not 500 during an incident.
"""
import pytest
from unittest.mock import patch

from django.db import DatabaseError
from django.urls import reverse


pytestmark = pytest.mark.django_db


class TestDatabaseOutageResilience:
    @patch("monitoring.views.get_service_statuses", side_effect=DatabaseError("db down"))
    def test_status_page_degrades_gracefully(self, _mock, client):
        response = client.get(reverse("monitoring:status"))
        # Not a 500 — a calm degraded page.
        assert response.status_code == 200
        content = response.content.decode()
        assert "temporarily unavailable" in content.lower()
        assert "monitoring/unavailable.html" in [t.name for t in response.templates]

    @patch("monitoring.views.get_service_statuses", side_effect=DatabaseError("db down"))
    def test_status_api_degrades_gracefully(self, _mock, client):
        response = client.get(reverse("monitoring:status_api"))
        assert response.status_code == 200
        data = response.json()
        assert data["overall_status"] == "unknown"
        assert data["services"] == []

    def test_healthz_has_no_db_dependency(self, client):
        # Even with the DB query patched to fail, healthz must answer.
        with patch("monitoring.views.get_service_statuses",
                   side_effect=DatabaseError("db down")):
            assert client.get(reverse("monitoring:healthz")).status_code == 200


class TestErrorPages:
    def test_404_template_is_self_contained(self, client):
        """The 404 page renders without template tags or DB."""
        response = client.get("/this-path-does-not-exist/")
        assert response.status_code == 404
        # Our branded copy, not Django's default debug 404.
        assert "Sefaria Status" in response.content.decode()
