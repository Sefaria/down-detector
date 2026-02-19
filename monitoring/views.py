"""
Views for the status page.
"""
from django.conf import settings
from django.db.models import Max
from django.http import HttpResponse
from django.views.decorators.cache import cache_page
from django.shortcuts import render
from django.urls import reverse

from monitoring.models import HealthCheck, Message


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
                "status": "unknown",
                "response_time_ms": None,
                "last_checked": None,
                "status_code": None,
                "error_message": "",
            })
            continue

        latest_check = recent_checks[0]

        # Service is "down" only if ALL of the last N checks failed
        all_down = (
            len(recent_checks) >= threshold
            and all(c.status == "down" for c in recent_checks)
        )
        confirmed_status = "down" if all_down else "up"

        statuses.append({
            "name": service_name,
            "status": confirmed_status,
            "response_time_ms": latest_check.response_time_ms,
            "last_checked": latest_check.checked_at,
            "status_code": latest_check.status_code,
            "error_message": latest_check.error_message if all_down else "",
        })

    return statuses


def get_overall_status(service_statuses: list[dict], active_incidents: list) -> str:
    """
    Determine the overall system status.
    
    Returns one of: "operational", "partial", "major"
    """
    # Check for high severity active incidents
    has_high_incident = any(i.severity == "high" for i in active_incidents)
    if has_high_incident:
        return "major"
    
    # Check for any services down
    any_down = any(s["status"] == "down" for s in service_statuses)
    if any_down:
        return "major"
    
    # Check for medium severity incidents
    has_medium_incident = any(i.severity == "medium" for i in active_incidents)
    if has_medium_incident:
        return "partial"
    
    return "operational"


def get_status_label(overall_status: str) -> str:
    """Convert status code to human-readable label."""
    return {
        "operational": "All Systems Operational",
        "partial": "Partial Issues",
        "major": "Major Outage",
    }.get(overall_status, "Unknown")


@cache_page(30)
def status_page(request):
    """
    Public status page showing service health and incidents.
    """
    # Get service statuses
    service_statuses = get_service_statuses()
    
    # Get active incidents
    active_incidents = list(Message.objects.filter(active=True).order_by("-created_at"))
    
    # Get recent resolved incidents (last 7 days)
    resolved_incidents = list(
        Message.objects
        .filter(active=False)
        .order_by("-updated_at")[:10]
    )
    
    # Calculate overall status
    overall_status = get_overall_status(service_statuses, active_incidents)
    status_label = get_status_label(overall_status)
    
    context = {
        "services": service_statuses,
        "active_incidents": active_incidents,
        "resolved_incidents": resolved_incidents,
        "overall_status": overall_status,
        "status_label": status_label,
    }
    
    return render(request, "monitoring/status.html", context)


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
