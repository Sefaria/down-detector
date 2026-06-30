"""
Tests for the 90-day uptime history timeline (views.get_uptime_history).
"""
import pytest
from datetime import timedelta

from django.utils import timezone

from monitoring.models import HealthCheck, Outage
from monitoring.views import get_uptime_history


pytestmark = pytest.mark.django_db


SERVICE = "test-service"  # matches config/settings/test.py MONITORED_SERVICES


def _day_starts(days=90):
    """Return (window_start, today_start) aligned to how the view buckets."""
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start - timedelta(days=days - 1), today_start


def _monitor_since(days_ago):
    """Seed an early HealthCheck so the service counts as monitored since then."""
    HealthCheck.objects.create(
        service_name=SERVICE,
        status="up",
        response_time_ms=100,
        status_code=200,
        checked_at=timezone.now() - timedelta(days=days_ago),
    )


class TestUptimeHistory:
    def test_no_outages_is_all_up(self):
        """A long-monitored service with no outages shows 90 'up' days at 100%."""
        _monitor_since(95)

        history = get_uptime_history(days=90)
        svc = next(s for s in history if s["name"] == SERVICE)

        assert len(svc["days"]) == 90
        assert all(d["status"] == "up" for d in svc["days"])
        assert svc["uptime_pct"] == 100.0

    def test_full_day_outage_is_down(self):
        """A 24h outage marks that day 'down' and dents the overall percentage."""
        _monitor_since(95)
        _, today_start = _day_starts()
        # A full calendar day, 5 days ago.
        start = today_start - timedelta(days=5)
        Outage.objects.create(
            service_name=SERVICE,
            start_time=start,
            end_time=start + timedelta(days=1),
            resolved=True,
        )

        svc = next(s for s in get_uptime_history(days=90) if s["name"] == SERVICE)
        down_days = [d for d in svc["days"] if d["status"] == "down"]

        assert len(down_days) == 1
        assert down_days[0]["uptime"] == 0.0
        assert svc["uptime_pct"] is not None and svc["uptime_pct"] < 100.0

    def test_brief_blip_is_partial(self):
        """A few-minute outage in a day shows as 'partial', not 'down'."""
        _monitor_since(95)
        _, today_start = _day_starts()
        start = today_start - timedelta(days=3) + timedelta(hours=12)
        Outage.objects.create(
            service_name=SERVICE,
            start_time=start,
            end_time=start + timedelta(minutes=3),
            resolved=True,
        )

        svc = next(s for s in get_uptime_history(days=90) if s["name"] == SERVICE)
        partial_days = [d for d in svc["days"] if d["status"] == "partial"]

        assert len(partial_days) == 1
        assert 99.0 <= partial_days[0]["uptime"] < 100.0

    def test_days_before_monitoring_are_nodata(self):
        """Days before the service was first seen are 'nodata', not fake green."""
        _monitor_since(3)  # only ~3 days of history

        svc = next(s for s in get_uptime_history(days=90) if s["name"] == SERVICE)

        # The oldest bucket (≈90 days ago) has no data.
        assert svc["days"][0]["status"] == "nodata"
        assert svc["days"][0]["uptime"] is None
        # The most recent bucket (today) does have data.
        assert svc["days"][-1]["status"] != "nodata"

    def test_no_data_at_all(self):
        """A service with no checks and no outages reports no data."""
        svc = next(s for s in get_uptime_history(days=90) if s["name"] == SERVICE)

        assert svc["has_data"] is False
        assert svc["uptime_pct"] is None
        assert all(d["status"] == "nodata" for d in svc["days"])

    def test_ongoing_outage_counts_to_now(self):
        """An unresolved (open) outage still counts as downtime up to now."""
        _monitor_since(95)
        Outage.objects.create(
            service_name=SERVICE,
            start_time=timezone.now() - timedelta(hours=6),
            end_time=None,
            resolved=False,
        )

        svc = next(s for s in get_uptime_history(days=90) if s["name"] == SERVICE)
        today = svc["days"][-1]

        assert today["status"] in ("partial", "down")
        assert today["uptime"] < 100.0
