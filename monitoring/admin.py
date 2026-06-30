"""
Django Admin configuration for monitoring models.
"""
import logging

from django import forms
from django.conf import settings
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import HealthCheck, Outage, Message, Maintenance


def _monitored_service_names() -> list[str]:
    return [s["name"] for s in getattr(settings, "MONITORED_SERVICES", [])]


def _pill(label: str, bg: str):
    """A solid-color pill with white text — readable on light *and* dark admin."""
    return format_html(
        '<span style="display:inline-block;min-width:74px;text-align:center;'
        'padding:2px 10px;border-radius:999px;background:{};color:#fff;'
        'font-weight:700;font-size:11px;">{}</span>',
        bg, label,
    )


# Status/severity colors chosen to keep white text legible on either theme.
_PILL_COLORS = {
    "up": "#2e7d52",
    "down": "#b3322f",
    "degraded": "#b07d18",
    "maintenance": "#2f6fb0",
    "unknown": "#6b6b6b",
    "high": "#b3322f",
    "medium": "#b07d18",
    "resolved": "#2e7d52",
}


class MaintenanceAdminForm(forms.ModelForm):
    """Pick affected services from checkboxes so they can't be mistyped.

    The model stores ``affected_services`` as a comma-separated string; this
    form presents the configured services as checkboxes and (de)serializes to
    that string. Selecting none means "all services".
    """

    affected_services = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select the services this covers. Select none to cover all services.",
    )

    class Meta:
        model = Maintenance
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["affected_services"].choices = [
            (name, name) for name in _monitored_service_names()
        ]
        if self.instance and self.instance.pk:
            self.initial["affected_services"] = self.instance.affected_list

    def clean_affected_services(self):
        return ", ".join(self.cleaned_data["affected_services"])

logger = logging.getLogger(__name__)

# Brand the admin so it's obviously the status monitor, not a generic Django site.
admin.site.site_header = "Sefaria Status — Administration"
admin.site.site_title = "Sefaria Status Admin"
admin.site.index_title = (
    "Monitoring data. Health checks and outages are recorded automatically; "
    "incident messages and maintenance windows are authored here."
)
# Landing page shows a live system-status dashboard above the model list.
admin.site.index_template = "admin/monitoring_index.html"


@admin.register(HealthCheck)
class HealthCheckAdmin(admin.ModelAdmin):
    """Admin for health check records (read-only)."""

    list_display = [
        "service_name",
        "status_badge",
        "response_time_ms",
        "status_code",
        "error_preview",
        "checked_at",
    ]
    list_filter = ["service_name", "status"]
    search_fields = ["service_name", "error_message"]
    date_hierarchy = "checked_at"
    ordering = ["-checked_at"]
    list_per_page = 50

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return _pill(obj.status.upper(), _PILL_COLORS.get(obj.status, "#6b6b6b"))

    @admin.display(description="Error")
    def error_preview(self, obj):
        msg = obj.error_message or ""
        return (msg[:60] + "…") if len(msg) > 60 else msg

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

    fieldsets = (
        (None, {
            "fields": (
                "service_name", "start_time", "end_time", "resolved",
                "duration_display", "created_at", "updated_at",
            ),
            "description": (
                "Outages are opened and closed automatically by the scheduler. "
                "These fields are read-only. To clear a stuck-open outage (e.g. a "
                "recovery was missed), select it in the list and run "
                "<strong>Force-resolve selected outages</strong> — the scheduler "
                "reconciles on its next cycle."
            ),
        }),
    )

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

    form = MaintenanceAdminForm

    list_display = [
        "title",
        "state",
        "scope",
        "start_time",
        "end_time",
        "active",
    ]
    list_filter = ["active"]
    search_fields = ["title", "description", "affected_services"]
    date_hierarchy = "start_time"
    ordering = ["-start_time"]
    list_editable = ["active"]

    fieldsets = (
        (None, {
            "fields": ("title", "description"),
            "description": "Shown on the public status page while the window is current or upcoming.",
        }),
        ("Scope", {
            "fields": ("affected_services",),
            "description": "Which services this covers. Select none to cover every monitored service.",
        }),
        ("Schedule", {
            "fields": ("start_time", "end_time", "active"),
            "description": (
                "All times UTC. While now is between start and end (and active is "
                "on), covered services show <strong>Under Maintenance</strong> and "
                "their Slack down/recovery alerts are suppressed. Uncheck "
                "<strong>active</strong> to cancel without deleting."
            ),
        }),
    )

    @admin.display(description="Scope")
    def scope(self, obj):
        """Affected services, or 'All services' when none are named."""
        return ", ".join(obj.affected_list) or "All services"

    @admin.display(description="State")
    def state(self, obj):
        """In progress / Scheduled / Past, at a glance (color-coded)."""
        if not obj.active:
            label, color = "Cancelled", "#6b6b6b"
        elif obj.is_in_progress():
            label, color = "In progress", "#2f6fb0"
        elif obj.is_upcoming():
            label, color = "Scheduled", "#b07d18"
        else:
            label, color = "Past", "#6b6b6b"
        return _pill(label, color)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """Admin for incident messages."""

    list_display = ["severity_badge", "text_preview", "active", "created_at", "updated_at"]
    list_filter = ["severity", "active"]
    list_editable = ["active"]
    search_fields = ["text"]
    ordering = ["-created_at"]
    actions = ["mark_as_resolved"]

    @admin.display(description="Severity", ordering="severity")
    def severity_badge(self, obj):
        return _pill(
            obj.get_severity_display(),
            _PILL_COLORS.get(obj.severity, "#6b6b6b"),
        )

    fieldsets = (
        (None, {
            "fields": ("severity", "text", "active"),
            "description": (
                "Incident banners shown on the public status page (and the "
                "RSS/Atom feed). <strong>High</strong> drives a Major Outage "
                "banner, <strong>Medium</strong> a Degraded Performance banner. "
                "Uncheck <strong>active</strong> (or use the resolve action) to "
                "move an incident into the history section."
            ),
        }),
    )

    @admin.display(description="Message")
    def text_preview(self, obj):
        """Show first 80 characters of the message text."""
        return obj.text[:80] + "..." if len(obj.text) > 80 else obj.text

    @admin.action(description="Mark selected messages as resolved")
    def mark_as_resolved(self, request, queryset):
        """Bulk action to resolve incidents."""
        updated = queryset.update(active=False, severity="resolved")
        self.message_user(request, f"{updated} message(s) marked as resolved.")
