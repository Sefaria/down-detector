"""
Template tags for the admin landing dashboard.
"""
from django import template

from monitoring.models import Outage, Message, Maintenance
from monitoring.views import (
    get_service_statuses,
    get_overall_status,
    get_status_label,
)

register = template.Library()


@register.inclusion_tag("admin/_status_dashboard.html")
def status_dashboard():
    """Live system snapshot shown at the top of the admin index."""
    services = get_service_statuses()
    active_incidents = list(Message.objects.filter(active=True))
    overall = get_overall_status(services, active_incidents)
    maintenance_now = [
        m for m in Maintenance.current_and_upcoming() if m.is_in_progress()
    ]
    return {
        "services": services,
        "overall_status": overall,
        "status_label": get_status_label(overall),
        "open_outages": Outage.objects.filter(resolved=False).count(),
        "active_incidents": len(active_incidents),
        "maintenance_now": maintenance_now,
    }
