"""
Tests for the CDN/edge-cache headers on the public endpoints.
"""
import pytest
from django.urls import reverse


pytestmark = pytest.mark.django_db


class TestCacheHeaders:
    def test_status_page_is_publicly_cacheable(self, client):
        r = client.get(reverse("monitoring:status"))
        cc = r.headers["Cache-Control"]
        assert "public" in cc
        assert "s-maxage=30" in cc
        assert "stale-while-revalidate=60" in cc
        # No per-user variance, so an edge can serve one copy to everyone.
        assert not r.cookies
        assert "Cookie" not in (r.headers.get("Vary") or "")

    def test_status_api_is_publicly_cacheable(self, client):
        r = client.get(reverse("monitoring:status_api"))
        cc = r.headers["Cache-Control"]
        assert "public" in cc
        assert "s-maxage=10" in cc
        assert "stale-while-revalidate=30" in cc
        assert not r.cookies
        assert "Cookie" not in (r.headers.get("Vary") or "")

    def test_healthz_is_never_cached(self, client):
        cc = client.get(reverse("monitoring:healthz")).headers["Cache-Control"]
        assert "no-store" in cc
