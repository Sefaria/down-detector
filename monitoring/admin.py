"""
Django Admin configuration for monitoring models.
"""
from django.contrib import admin

from .models import HealthCheck, Message


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
