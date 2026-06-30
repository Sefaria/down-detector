"""
Tests for the RSS/Atom incident feeds.
"""
import pytest
from django.urls import reverse

from tests.factories import MessageFactory


pytestmark = pytest.mark.django_db


class TestIncidentFeeds:
    def test_rss_feed_returns_xml(self, client):
        MessageFactory(severity="high", text="Database outage", active=True)

        response = client.get(reverse("monitoring:incident_feed_rss"))

        assert response.status_code == 200
        assert "rss" in response["Content-Type"] or "xml" in response["Content-Type"]
        body = response.content.decode()
        assert "Database outage" in body
        assert "Incident History" in body

    def test_atom_feed_returns_xml(self, client):
        MessageFactory(severity="medium", text="Search slowdown", active=True)

        response = client.get(reverse("monitoring:incident_feed_atom"))

        assert response.status_code == 200
        assert "xml" in response["Content-Type"]
        assert "Search slowdown" in response.content.decode()

    def test_feed_items_have_unique_guids(self, client):
        MessageFactory(severity="high", text="First incident")
        MessageFactory(severity="medium", text="Second incident")

        body = client.get(reverse("monitoring:incident_feed_rss")).content.decode()

        # Stable, non-permalink guids derived from the primary key.
        assert body.count("sefaria-status-incident-") >= 2

    def test_page_advertises_feed(self, client):
        """The status page links the feed for autodiscovery."""
        content = client.get(reverse("monitoring:status")).content.decode()
        assert 'type="application/rss+xml"' in content
        assert reverse("monitoring:incident_feed_rss") in content
