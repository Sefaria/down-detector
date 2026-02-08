"""
Views for the status page.
"""
from django.conf import settings
from django.db.models import Max
from django.views.decorators.cache import cache_page
from django.shortcuts import render

from monitoring.models import HealthCheck, Message


def get_service_statuses() -> list[dict]:
    """
    Get the latest status for each monitored service.
    
    Returns list of dicts with service info and status.
    """
    services = getattr(settings, "MONITORED_SERVICES", [])
    statuses = []
    
    for config in services:
        service_name = config["name"]
        
        # Get the latest health check for this service
        latest_check = (
            HealthCheck.objects
            .filter(service_name=service_name)
            .order_by("-checked_at")
            .first()
        )
        
        statuses.append({
            "name": service_name,
            "status": latest_check.status if latest_check else "unknown",
            "response_time_ms": latest_check.response_time_ms if latest_check else None,
            "last_checked": latest_check.checked_at if latest_check else None,
            "status_code": latest_check.status_code if latest_check else None,
            "error_message": latest_check.error_message if latest_check else "",
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
