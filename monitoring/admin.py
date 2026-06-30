"""
Django Admin configuration for monitoring models.
"""
import logging

from django.contrib import admin
from django.utils import timezone

from .models import HealthCheck, Outage, Message, Maintenance

logger = logging.getLogger(__name__)


@admin.register(HealthCheck)
class HealthCheckAdmin(admin.ModelAdmin):
    """Admin for health check records (read-only)."""

    list_display = [
        "service_name",
        "status",
        "response_time_ms",
        "status_code",
        "checked_at",
    ]
    list_filter = ["service_name", "status"]
    search_fields = ["service_name", "error_message"]
    date_hierarchy = "checked_at"
    ordering = ["-checked_at"]

    # Health checks are system-generated, not manually edited
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Allow deletion for cleanup purposes
        return request.user.is_superuser


@admin.register(Outage)
class OutageAdmin(admin.ModelAdmin):
    """Admin for outage records.

    Outages are opened and resolved automatically by the StateTracker.
    Operators cannot create or hand-edit fields (that would diverge from
    the tracker), but they can **force-resolve** a stuck outage via the
    ``resolve_outages`` action — for example when a recovery was missed
    and the record is dangling open. The scheduler reconciles with the
    database each cycle, so closing the record here also clears the
    monitor's in-memory down state.
    """

    list_display = [
        "service_name",
        "start_time",
        "end_time",
        "resolved",
        "duration_display",
    ]
    list_filter = ["service_name", "resolved"]
    search_fields = ["service_name"]
    date_hierarchy = "start_time"
    ordering = ["-start_time"]
    actions = ["resolve_outages"]

    # Every field is read-only: the only sanctioned mutation is the
    # resolve action below, so manual edits can't desync from the tracker.
    readonly_fields = [
        "service_name",
        "start_time",
        "end_time",
        "resolved",
        "duration_display",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="Duration")
    def duration_display(self, obj):
        """Human-readable outage duration (e.g. '2h 15m', '45m 30s')."""
        total_seconds = int(obj.duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    @admin.action(
        description="Force-resolve selected outages (close stuck incidents)",
        permissions=["change"],
    )
    def resolve_outages(self, request, queryset):
        """
        Manually close the selected open outages.

        Sets ``end_time`` (if not already set) and ``resolved=True`` for
        each currently-open outage. The scheduler picks this up on its next
        check cycle: it clears the in-memory down state and, if the service
        is in fact still failing, opens a fresh outage and re-alerts.
        Already-resolved outages in the selection are skipped.
        """
        now = timezone.now()
        resolved = 0
        for outage in queryset.filter(resolved=False):
            outage.end_time = outage.end_time or now
            outage.resolved = True
            outage.save()
            resolved += 1
            logger.warning(
                f"Outage {outage.pk} for {outage.service_name} force-resolved "
                f"from admin by {request.user}"
            )

        if resolved:
            self.message_user(
                request, f"{resolved} outage(s) force-resolved."
            )
        else:
            self.message_user(
                request, "No open outages were selected; nothing to resolve."
            )

    # Outages are system-generated; no manual creation.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Permission is required for the resolve action to be available on
        # the changelist (obj is None). Object detail pages stay read-only
        # because every field is in ``readonly_fields``.
        return True

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    """Admin for operator-scheduled maintenance windows."""

    list_display = [
        "title",
        "state",
        "affected_services",
        "start_time",
        "end_time",
        "active",
    ]
    list_filter = ["active"]
    search_fields = ["title", "description", "affected_services"]
    date_hierarchy = "start_time"
    ordering = ["-start_time"]
    list_editable = ["active"]

    @admin.display(description="State")
    def state(self, obj):
        """In progress / Scheduled / Past, at a glance."""
        if not obj.active:
            return "Cancelled"
        if obj.is_in_progress():
            return "In progress"
        if obj.is_upcoming():
            return "Scheduled"
        return "Past"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """Admin for incident messages."""

    list_display = ["severity", "text_preview", "active", "created_at", "updated_at"]
    list_filter = ["severity", "active"]
    list_editable = ["active"]
    search_fields = ["text"]
    ordering = ["-created_at"]
    actions = ["mark_as_resolved"]

    @admin.display(description="Message")
    def text_preview(self, obj):
        """Show first 80 characters of the message text."""
        return obj.text[:80] + "..." if len(obj.text) > 80 else obj.text

    @admin.action(description="Mark selected messages as resolved")
    def mark_as_resolved(self, request, queryset):
        """Bulk action to resolve incidents."""
        updated = queryset.update(active=False, severity="resolved")
        self.message_user(request, f"{updated} message(s) marked as resolved.")
