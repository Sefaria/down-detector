"""
Django Admin configuration for monitoring models.
"""
from django.contrib import admin

from .models import HealthCheck, Outage, Message


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
    """Admin for outage records (read-only).

    Outages are opened and resolved automatically by the StateTracker,
    which holds the authoritative state in memory. They are exposed here
    for inspection only — manual edits could diverge from the tracker.
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

    # Outages are system-generated, not manually created or edited.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


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
