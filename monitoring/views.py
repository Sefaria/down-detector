"""
Views for the status page.
"""
import logging
import random
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Min, Q
from django.http import HttpResponse, JsonResponse
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.shortcuts import render
from django.urls import reverse

from monitoring.models import HealthCheck, Message, Outage, Maintenance

logger = logging.getLogger(__name__)


# -- Status-page quotes (Hebrew, English, Sefaria ref, display source) ------

QUOTES_OPERATIONAL = [
    {
        "hebrew": "הִנֵּה לֹא־יָנוּם וְלֹא יִישָׁן שׁוֹמֵר יִשְׂרָאֵל",
        "english": "The Guardian of Israel neither slumbers nor sleeps.",
        "ref": "Psalms.121.4",
        "source": "Psalms 121:4",
    },
    {
        "hebrew": "וַיַּרְא אֱלֹהִים אֶת־כׇּל־אֲשֶׁר עָשָׂה וְהִנֵּה־טוֹב מְאֹד",
        "english": "And God saw all that He had made, and behold, it was very good.",
        "ref": "Genesis.1.31",
        "source": "Genesis 1:31",
    },
    {
        "hebrew": "כִּי־יָשָׁר דְּבַר־יְהֹוָה וְכׇל־מַעֲשֵׂהוּ בֶּאֱמוּנָה",
        "english": "For the word of the Lord is right, and all His work is done in faithfulness.",
        "ref": "Psalms.33.4",
        "source": "Psalms 33:4",
    },
    {
        "hebrew": "נֵר־לְרַגְלִי דְבָרֶךָ וְאוֹר לִנְתִיבָתִי",
        "english": "Your word is a lamp to my feet, and a light to my path.",
        "ref": "Psalms.119.105",
        "source": "Psalms 119:105",
    },
    {
        "hebrew": "יְהֹוָה יִשְׁמׇר־צֵאתְךָ וּבוֹאֶךָ מֵעַתָּה וְעַד־עוֹלָם",
        "english": "The Lord will guard your going out and coming in, from now and forever.",
        "ref": "Psalms.121.8",
        "source": "Psalms 121:8",
    },
    {
        "hebrew": "וַיֹּאמֶר אֱלֹהִים יְהִי אוֹר וַיְהִי־אוֹר",
        "english": "And God said: Let there be light — and there was light.",
        "ref": "Genesis.1.3",
        "source": "Genesis 1:3",
    }
]

QUOTES_PARTIAL = [
    {
        "hebrew": "כִּי שֶׁבַע יִפּוֹל צַדִּיק וָקָם",
        "english": "Seven times the righteous falls and rises again.",
        "ref": "Proverbs.24.16",
        "source": "Proverbs 24:16",
    },
    {
        "hebrew": "כִּי נָפַלְתִּי קָמְתִּי כִּי־אֵשֵׁב בַּחֹשֶׁךְ יְהֹוָה אוֹר לִי",
        "english": "When I fall, I shall arise; when I sit in darkness, the Lord is a light unto me.",
        "ref": "Micah.7.8",
        "source": "Micah 7:8",
    },
    {
        "hebrew": "בָּעֶרֶב יָלִין בֶּכִי וְלַבֹּקֶר רִנָּה",
        "english": "Weeping may tarry for the night, but joy comes in the morning.",
        "ref": "Psalms.30.6",
        "source": "Psalms 30:6",
    },
    {
        "hebrew": "וְקוֹיֵ יְהֹוָה יַחֲלִיפוּ כֹחַ יַעֲלוּ אֵבֶר כַּנְּשָׁרִים",
        "english": "They that wait upon the Lord shall renew their strength; they shall mount up with wings as eagles.",
        "ref": "Isaiah.40.31",
        "source": "Isaiah 40:31",
    },
]

QUOTES_MAJOR = [
    {
        "hebrew": "לֹא עָלֶיךָ הַמְּלָאכָה לִגְמֹר, וְלֹא אַתָּה בֶן חוֹרִין לִבָּטֵל מִמֶּנָּה",
        "english": "It is not your duty to finish the work, but neither are you at liberty to neglect it.",
        "ref": "Pirkei_Avot.2.16",
        "source": "Pirkei Avot 2:16",
    },
    {
        "hebrew": "קַוֵּה אֶל־יְהֹוָה חֲזַק וְיַאֲמֵץ לִבֶּךָ וְקַוֵּה אֶל־יְהֹוָה",
        "english": "Wait for the Lord; be strong and let your heart take courage.",
        "ref": "Psalms.27.14",
        "source": "Psalms 27:14",
    },
    {
        "hebrew": "אַל־תִּירָא כִּי עִמְּךָ־אָנִי",
        "english": "Fear not, for I am with you.",
        "ref": "Isaiah.41.10",
        "source": "Isaiah 41:10",
    },
]

QUOTES_BY_STATUS = {
    "operational": QUOTES_OPERATIONAL,
    "degraded": QUOTES_PARTIAL,
    "partial": QUOTES_PARTIAL,
    "major": QUOTES_MAJOR,
}


def get_random_quote(overall_status: str) -> dict:
    """Pick a random quote appropriate for the current status."""
    quotes = QUOTES_BY_STATUS.get(overall_status, QUOTES_OPERATIONAL)
    return random.choice(quotes)


def get_public_status_detail(error_message: str, status_code: int | None) -> str:
    """
    Turn a raw internal check error into a safe, user-facing hint.

    The raw ``error_message`` is operator-facing and can contain internal
    infrastructure details (private hostnames/IPs, library stack fragments,
    e.g. ``connection to server at "10.0.3.3", port 5432 failed``). The
    *public* status page must never echo those. We map the error to a short,
    generic phrase and surface only the non-sensitive HTTP status code when
    one is present. The full raw error remains available to authenticated
    operators in the Django admin (``HealthCheck``).
    """
    if not error_message:
        return ""

    msg = error_message.lower()
    if "timed out" in msg or "timeout" in msg:
        return "Request timed out"
    if "connection" in msg or "unreachable" in msg:
        return "Service unreachable"
    if status_code:
        # Covers "Expected 200, got 521" etc. The code itself is not sensitive.
        return f"Unexpected response (HTTP {status_code})"
    return "Not responding correctly"


def get_service_statuses() -> list[dict]:
    """
    Get the confirmed status for each monitored service.

    A service is shown as "down" only if the last N checks all failed,
    where N is the service's ``failure_threshold`` (same threshold used
    for Slack alerts).  This prevents brief blips from flashing red on
    the public status page.

    Returns list of dicts with service info and status.
    """
    services = getattr(settings, "MONITORED_SERVICES", [])
    default_threshold = getattr(
        settings, "ALERT_AFTER_CONSECUTIVE_FAILURES", 2
    )
    default_degraded_ms = getattr(settings, "DEGRADED_RESPONSE_MS", 2000)
    maint_services = Maintenance.services_under_maintenance()
    statuses = []

    for config in services:
        service_name = config["name"]
        threshold = config.get("failure_threshold", default_threshold)

        # Get the last N health checks for this service
        recent_checks = list(
            HealthCheck.objects
            .filter(service_name=service_name)
            .order_by("-checked_at")[:threshold]
        )

        if not recent_checks:
            statuses.append({
                "name": service_name,
                "status": "maintenance" if service_name in maint_services else "unknown",
                "response_time_ms": None,
                "last_checked": None,
                "status_code": None,
                "detail": "Under maintenance" if service_name in maint_services else "",
            })
            continue

        latest_check = recent_checks[0]

        # Service is "down" only if ALL of the last N checks failed
        all_down = (
            len(recent_checks) >= threshold
            and all(c.status == "down" for c in recent_checks)
        )

        # A confirmed-up service whose latest response time exceeds the
        # degraded threshold is shown as "degraded" (up but slow). This is a
        # page-only signal and never triggers a Slack alert.
        degraded_ms = config.get("degraded_threshold_ms", default_degraded_ms)
        rt = latest_check.response_time_ms
        if all_down:
            confirmed_status = "down"
            detail = get_public_status_detail(
                latest_check.error_message, latest_check.status_code
            )
        elif latest_check.status == "up" and rt and rt > degraded_ms:
            confirmed_status = "degraded"
            detail = f"Elevated response time ({rt}ms)"
        else:
            confirmed_status = "up"
            detail = ""

        # An active maintenance window overrides the measured state: planned
        # work is shown as "Under Maintenance", not down/degraded.
        if service_name in maint_services:
            confirmed_status = "maintenance"
            detail = "Under maintenance"

        statuses.append({
            "name": service_name,
            "status": confirmed_status,
            "response_time_ms": latest_check.response_time_ms,
            "last_checked": latest_check.checked_at,
            "status_code": latest_check.status_code,
            # Public, sanitized hint only — never the raw internal error.
            "detail": detail,
        })

    return statuses


def get_overall_status(service_statuses: list[dict], active_incidents: list) -> str:
    """
    Determine the overall system status.

    Returns one of: "operational", "degraded", "partial", "major",
    "maintenance".

    - "major"      — a high-severity incident, or *every* service is down.
    - "partial"    — some (but not all) services are down.
    - "maintenance"— nothing down, but a service is under maintenance.
    - "degraded"   — nothing down/maintenance, but a service is slow or a
                     medium-severity incident is active.
    - "operational"— everything healthy.
    """
    statuses = [s["status"] for s in service_statuses]
    total = len(statuses)
    down = sum(1 for s in statuses if s == "down")
    degraded = sum(1 for s in statuses if s == "degraded")
    maintenance = sum(1 for s in statuses if s == "maintenance")

    has_high_incident = any(i.severity == "high" for i in active_incidents)
    has_medium_incident = any(i.severity == "medium" for i in active_incidents)

    if has_high_incident or (total > 0 and down == total):
        return "major"
    if down > 0:
        return "partial"
    if maintenance > 0:
        return "maintenance"
    if degraded > 0 or has_medium_incident:
        return "degraded"
    return "operational"


def get_status_label(overall_status: str) -> str:
    """Convert status code to human-readable label."""
    return {
        "operational": "All Systems Operational",
        "degraded": "Degraded Performance",
        "partial": "Partial Outage",
        "major": "Major Outage",
        "maintenance": "Under Maintenance",
    }.get(overall_status, "Unknown")


def _latest_check_iso(service_statuses: list[dict]) -> str:
    """ISO-8601 timestamp of the most recent check across all services."""
    check_times = [s["last_checked"] for s in service_statuses if s["last_checked"]]
    return max(check_times).isoformat() if check_times else ""


def _format_seconds(total_seconds: int) -> str:
    """Human-readable duration: '45s', '12m', '2h 5m'."""
    total_seconds = max(0, total_seconds)
    if total_seconds < 60:
        return f"{total_seconds}s"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# Below this fraction of a day up, the day's bar is "down" (major); any
# downtime above it is shown as "partial" (a minor blip).
_UPTIME_PARTIAL_FLOOR = 0.99


def get_uptime_history(days: int = 90) -> list[dict]:
    """
    Per-service daily uptime for the last ``days``, for the timeline bars.

    Downtime is derived from ``Outage`` records (one row per confirmed
    downtime period), not from raw ``HealthCheck`` samples: outages persist
    indefinitely and record true downtime, whereas health-check rows are
    pruned at the retention horizon. Each day is classified as:

    - ``up``      — no recorded downtime that day.
    - ``partial`` — some downtime, but the day was >= 99% up (a brief blip).
    - ``down``    — the day was < 99% up (a substantial outage).
    - ``nodata``  — the service was not yet monitored that day.

    "Monitored since" is the earliest activity we can see for the service
    (earliest outage or earliest surviving health check). Days before it are
    ``nodata`` rather than fake green, so the timeline never claims uptime
    for days it has no evidence for.
    """
    services = getattr(settings, "MONITORED_SERVICES", [])
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today_start - timedelta(days=days - 1)

    history: list[dict] = []
    for cfg in services:
        name = cfg["name"]

        first_hc = HealthCheck.objects.filter(service_name=name).aggregate(
            m=Min("checked_at")
        )["m"]
        first_outage = Outage.objects.filter(service_name=name).aggregate(
            m=Min("start_time")
        )["m"]
        candidates = [t for t in (first_hc, first_outage) if t is not None]
        monitored_since = min(candidates) if candidates else None

        # Outages overlapping [window_start, now): start before now, and
        # either still open or ended after the window began.
        outages = list(
            Outage.objects
            .filter(service_name=name, start_time__lt=now)
            .filter(Q(end_time__gte=window_start) | Q(end_time__isnull=True))
            .only("start_time", "end_time")
        )

        day_buckets: list[dict] = []
        total_down = 0.0
        total_tracked = 0.0

        for i in range(days):
            day_start = window_start + timedelta(days=i)
            day_end = day_start + timedelta(days=1)
            # The most recent bucket only counts up to "now".
            effective_end = min(day_end, now)

            # No data for days entirely before monitoring began.
            if monitored_since is None or monitored_since >= day_end:
                day_buckets.append({
                    "date": day_start.date().isoformat(),
                    "status": "nodata",
                    "uptime": None,
                })
                continue

            # Clamp the first partial day of monitoring to when it started.
            effective_start = max(day_start, monitored_since)
            day_seconds = (effective_end - effective_start).total_seconds()
            if day_seconds <= 0:
                day_buckets.append({
                    "date": day_start.date().isoformat(),
                    "status": "nodata",
                    "uptime": None,
                })
                continue

            down = 0.0
            for o in outages:
                o_end = o.end_time or now
                overlap_start = max(effective_start, o.start_time)
                overlap_end = min(effective_end, o_end)
                if overlap_end > overlap_start:
                    down += (overlap_end - overlap_start).total_seconds()
            down = min(down, day_seconds)
            uptime = 1.0 - (down / day_seconds)

            if down <= 0:
                status = "up"
            elif uptime >= _UPTIME_PARTIAL_FLOOR:
                status = "partial"
            else:
                status = "down"

            day_buckets.append({
                "date": day_start.date().isoformat(),
                "status": status,
                "uptime": round(uptime * 100, 2),
            })
            total_down += down
            total_tracked += day_seconds

        overall_uptime = (
            round((1.0 - total_down / total_tracked) * 100, 2)
            if total_tracked > 0
            else None
        )
        history.append({
            "name": name,
            "days": day_buckets,
            "uptime_pct": overall_uptime,
            "has_data": total_tracked > 0,
        })

    return history


def get_response_time_sparklines(
    points: int = 40,
    hours: int = 24,
    width: float = 120.0,
    height: float = 28.0,
    pad: float = 3.0,
) -> dict:
    """
    Build a small inline-SVG response-time sparkline for each service.

    Returns ``{service_name: spark_dict | None}`` where ``spark_dict`` has the
    SVG geometry (a ``points`` string for the line and an ``area`` path for the
    fill) plus ``min``/``max``/``latest`` (ms) for the tooltip. The newest
    ``points`` samples within the last ``hours`` are used; a higher response
    time draws higher on the y-axis, so latency spikes read as upward peaks.
    Pure geometry computed here — the template just drops it into an <svg>, so
    there's no charting library or client-side work.
    """
    services = getattr(settings, "MONITORED_SERVICES", [])
    since = timezone.now() - timedelta(hours=hours)
    out: dict = {}

    for cfg in services:
        name = cfg["name"]
        values = list(
            HealthCheck.objects
            .filter(
                service_name=name,
                checked_at__gte=since,
                response_time_ms__isnull=False,
            )
            .order_by("-checked_at")
            .values_list("response_time_ms", flat=True)[:points]
        )

        if len(values) < 2:
            out[name] = None
            continue

        values.reverse()  # oldest -> newest, left to right
        vmin, vmax = min(values), max(values)
        span = (vmax - vmin) or 1
        n = len(values)
        usable_w = width - 2 * pad
        usable_h = height - 2 * pad
        baseline = height - pad

        coords = []
        for i, v in enumerate(values):
            x = pad + usable_w * i / (n - 1)
            norm = (v - vmin) / span  # 0..1, higher ms -> higher
            y = pad + usable_h * (1 - norm)
            coords.append((round(x, 1), round(y, 1)))

        line = " ".join(f"{x},{y}" for x, y in coords)
        area = (
            f"M {coords[0][0]},{baseline} "
            + " ".join(f"L {x},{y}" for x, y in coords)
            + f" L {coords[-1][0]},{baseline} Z"
        )

        out[name] = {
            "points": line,
            "area": area,
            "min": vmin,
            "max": vmax,
            "latest": values[-1],
            "count": n,
            "window": f"{hours}h",
            "width": width,
            "height": height,
        }

    return out


@cache_page(30)
def status_page(request):
    """
    Public status page showing service health and incidents.

    A status page must survive its *own* database being down, so all DB access
    is guarded: on a database error we serve a static "temporarily unavailable"
    page (HTTP 200) instead of a 500.
    """
    og_image_url = request.build_absolute_uri(static("img/og-image.png"))
    try:
        # Get service statuses, and attach a response-time sparkline to each.
        service_statuses = get_service_statuses()
        sparklines = get_response_time_sparklines()
        for s in service_statuses:
            s["sparkline"] = sparklines.get(s["name"])

        active_incidents = list(
            Message.objects.filter(active=True).order_by("-created_at")
        )
        maintenance_windows = list(Maintenance.current_and_upcoming())
        resolved_incidents = list(
            Message.objects.filter(active=False).order_by("-updated_at")[:10]
        )
        # Attach a human-readable duration to each resolved incident.
        for inc in resolved_incidents:
            inc.duration_str = _format_seconds(
                int((inc.updated_at - inc.created_at).total_seconds())
            )

        overall_status = get_overall_status(service_statuses, active_incidents)
        last_checked_iso = _latest_check_iso(service_statuses)

        uptime_history = get_uptime_history()
        uptime_values = [
            h["uptime_pct"] for h in uptime_history if h["uptime_pct"] is not None
        ]
        overall_uptime = (
            round(sum(uptime_values) / len(uptime_values), 2)
            if uptime_values else None
        )
    except DatabaseError:
        logger.exception("status_page: database unavailable; serving degraded page")
        return render(request, "monitoring/unavailable.html",
                      {"og_image_url": og_image_url})

    context = {
        "services": service_statuses,
        "active_incidents": active_incidents,
        "resolved_incidents": resolved_incidents,
        "overall_status": overall_status,
        "status_label": get_status_label(overall_status),
        "quote": get_random_quote(overall_status),
        "last_checked_iso": last_checked_iso,
        "uptime_history": uptime_history,
        "uptime_days": 90,
        "overall_uptime": overall_uptime,
        "maintenance_windows": maintenance_windows,
        # Absolute URL for social-preview crawlers (they reject relative paths).
        "og_image_url": og_image_url,
    }

    return render(request, "monitoring/status.html", context)


@cache_page(10)
def status_api(request):
    """
    Lightweight JSON snapshot of service health for live polling.

    The status page polls this every ~20s and updates the DOM in place,
    instead of reloading the whole page. It returns only the fast-changing
    bits (per-service status + overall banner + last-checked); slow-changing
    content (incidents, quote, uptime history) is refreshed by a much less
    frequent full reload. Mirrors the same confirmation logic as the page so
    the two never disagree. Degrades to an "unknown" snapshot if the DB is down.
    """
    try:
        service_statuses = get_service_statuses()
        active_incidents = list(Message.objects.filter(active=True))
        overall_status = get_overall_status(service_statuses, active_incidents)
        return JsonResponse({
            "overall_status": overall_status,
            "status_label": get_status_label(overall_status),
            "last_checked": _latest_check_iso(service_statuses),
            "services": [
                {
                    "name": s["name"],
                    "status": s["status"],
                    "response_time_ms": s["response_time_ms"],
                    "detail": s["detail"],
                }
                for s in service_statuses
            ],
        })
    except DatabaseError:
        logger.exception("status_api: database unavailable")
        return JsonResponse({
            "overall_status": "unknown",
            "status_label": "Status temporarily unavailable",
            "last_checked": "",
            "services": [],
        })


def healthz(request):
    """
    Lightweight liveness probe for the container HEALTHCHECK.

    Returns 200 without touching the database or rendering the page, so the web
    container comes up fast on deploy and a transient DB blip doesn't mark the
    process unhealthy. (Service health is tracked separately by the scheduler.)
    """
    return HttpResponse("ok", content_type="text/plain")


@cache_page(60 * 60)
def robots_txt(request):
    """Serve robots.txt file."""
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {settings.STATUS_PAGE_URL}/sitemap.xml",
    ]
    return HttpResponse("\\n".join(lines), content_type="text/plain")


def sitemap_xml(request):
    """Serve sitemap.xml file."""
    # We only have one page right now
    url = settings.STATUS_PAGE_URL
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{url}/</loc>
    <changefreq>always</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>'''
    return HttpResponse(xml, content_type="application/xml")
