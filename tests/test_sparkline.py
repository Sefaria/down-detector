"""
Tests for response-time sparklines (views.get_response_time_sparklines).
"""
import pytest
from datetime import timedelta

from django.utils import timezone

from monitoring.models import HealthCheck
from monitoring.views import get_response_time_sparklines


pytestmark = pytest.mark.django_db


SERVICE = "test-service"  # from config/settings/test.py MONITORED_SERVICES


def _check(ms, minutes_ago, status="up"):
    HealthCheck.objects.create(
        service_name=SERVICE,
        status=status,
        response_time_ms=ms,
        status_code=200 if status == "up" else 503,
        checked_at=timezone.now() - timedelta(minutes=minutes_ago),
    )


class TestSparklines:
    def test_none_when_too_few_points(self):
        """A single data point is not enough to draw a line."""
        _check(100, 1)
        spark = get_response_time_sparklines()[SERVICE]
        assert spark is None

    def test_builds_geometry_and_stats(self):
        """min/max/latest and an SVG points string are produced."""
        _check(100, 30)
        _check(300, 20)
        _check(200, 10)  # latest

        spark = get_response_time_sparklines()[SERVICE]

        assert spark is not None
        assert spark["min"] == 100
        assert spark["max"] == 300
        assert spark["latest"] == 200
        assert spark["count"] == 3
        # 3 points -> 3 "x,y" pairs in the polyline.
        assert len(spark["points"].split(" ")) == 3
        assert spark["area"].startswith("M ")
        assert spark["area"].endswith("Z")

    def test_higher_ms_draws_higher(self):
        """A larger response time maps to a smaller y (peaks point up)."""
        _check(50, 20)    # min -> bottom (large y)
        _check(500, 10)   # max -> top (small y)

        spark = get_response_time_sparklines()[SERVICE]
        pairs = [p.split(",") for p in spark["points"].split(" ")]
        y_first = float(pairs[0][1])   # the 50ms point
        y_last = float(pairs[-1][1])   # the 500ms point

        assert y_last < y_first

    def test_ignores_null_response_times(self):
        """Checks with no response time (e.g. connection failures) are skipped."""
        _check(100, 30)
        _check(200, 20)
        HealthCheck.objects.create(
            service_name=SERVICE, status="down", response_time_ms=None,
            status_code=None, checked_at=timezone.now() - timedelta(minutes=5),
        )

        spark = get_response_time_sparklines()[SERVICE]
        assert spark["count"] == 2

    def test_respects_time_window(self):
        """Samples older than the window are excluded."""
        _check(100, 30)
        _check(200, 20)
        _check(999, 60 * 48)  # 48h ago, outside the default 24h window

        spark = get_response_time_sparklines(hours=24)[SERVICE]
        assert spark["count"] == 2
        assert spark["max"] == 200
