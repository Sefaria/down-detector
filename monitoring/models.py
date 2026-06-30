"""
Models for the Sefaria status monitoring system.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class HealthCheck(models.Model):
    """
    Records the result of a health check for a monitored service.
    """

    STATUS_CHOICES = [
        ("up", "Up"),
        ("down", "Down"),
    ]

    service_name = models.CharField(max_length=100, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    response_time_ms = models.PositiveIntegerField(null=True, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    checked_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-checked_at"]
        get_latest_by = "checked_at"
        indexes = [
            models.Index(fields=["service_name", "-checked_at"]),
            models.Index(fields=["-checked_at"]),
        ]
        verbose_name = "Health Check"
        verbose_name_plural = "Health Checks"

    def __str__(self):
        return f"{self.service_name} - {self.status.upper()} @ {self.checked_at:%Y-%m-%d %H:%M:%S}"


class Outage(models.Model):
    """
    Records an explicit period of downtime for a service.
    """
    
    service_name = models.CharField(max_length=100, db_index=True)
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(null=True, blank=True)
    resolved = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_time"]
        indexes = [
            models.Index(fields=["service_name", "resolved", "-start_time"]),
        ]
        verbose_name = "Outage"
        verbose_name_plural = "Outages"

    def __str__(self):
        status = "RESOLVED" if self.resolved else "ACTIVE"
        return f"{self.service_name} - {status} from {self.start_time:%Y-%m-%d %H:%M:%S}"

    @property
    def duration(self):
        """Returns the duration of the outage."""
        if not self.end_time:
            from django.utils import timezone
            return timezone.now() - self.start_time
        return self.end_time - self.start_time


class Maintenance(models.Model):
    """
    An operator-scheduled maintenance window for one or more services.

    While a window is in progress, affected services are shown as "Under
    Maintenance" on the status page and their down/recovery Slack alerts are
    suppressed (planned work should not page anyone). Authored in the admin.
    """

    title = models.CharField(
        max_length=200,
        help_text="Short summary shown on the status page, e.g. 'Database upgrade'.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional detail shown beneath the title on the status page.",
    )
    affected_services = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Comma-separated service names exactly as in MONITORED_SERVICES "
            "(e.g. 'MCP Server, Linker'). Leave blank to cover all services."
        ),
    )
    start_time = models.DateTimeField(
        db_index=True,
        help_text=(
            "When the window begins (UTC). While now is between start and end, "
            "affected services show 'Under Maintenance' and their alerts are "
            "suppressed."
        ),
    )
    end_time = models.DateTimeField(help_text="When the window ends (UTC).")
    active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Uncheck to cancel this window without deleting it.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_time"]
        indexes = [
            models.Index(fields=["active", "start_time", "end_time"]),
        ]
        verbose_name = "Maintenance Window"
        verbose_name_plural = "Maintenance Windows"

    def __str__(self):
        return f"{self.title} ({self.start_time:%Y-%m-%d %H:%M} UTC)"

    @property
    def affected_list(self) -> list[str]:
        """Service names this window covers; empty list means *all* services."""
        return [s.strip() for s in self.affected_services.split(",") if s.strip()]

    def covers(self, service_name: str) -> bool:
        """True if this window applies to the given service (blank = all)."""
        affected = self.affected_list
        return not affected or service_name in affected

    def is_in_progress(self, now=None) -> bool:
        now = now or timezone.now()
        return self.active and self.start_time <= now <= self.end_time

    def is_upcoming(self, now=None) -> bool:
        now = now or timezone.now()
        return self.active and self.start_time > now

    @classmethod
    def current_and_upcoming(cls, now=None):
        """Active windows that are in progress or scheduled for the future."""
        now = now or timezone.now()
        return cls.objects.filter(active=True, end_time__gte=now).order_by("start_time")

    @classmethod
    def services_under_maintenance(cls, now=None) -> set[str]:
        """
        Names of services currently inside an in-progress maintenance window.

        A window with a blank ``affected_services`` covers every configured
        service, so we expand it against ``MONITORED_SERVICES``.
        """
        now = now or timezone.now()
        in_progress = cls.objects.filter(
            active=True, start_time__lte=now, end_time__gte=now
        )
        all_names = [s["name"] for s in getattr(settings, "MONITORED_SERVICES", [])]
        covered: set[str] = set()
        for window in in_progress:
            affected = window.affected_list
            if not affected:
                return set(all_names)
            covered.update(affected)
        return covered


class Message(models.Model):
    """
    An incident message for the status page.
    """

    SEVERITY_CHOICES = [
        ("high", "High"),
        ("medium", "Medium"),
        ("resolved", "Resolved"),
    ]

    severity = models.CharField(
        max_length=20,
        choices=SEVERITY_CHOICES,
        help_text="High shows the banner as a Major Outage; Medium as Degraded Performance.",
    )
    text = models.TextField(
        help_text="Shown verbatim on the public status page and in the RSS/Atom feed.",
    )
    active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Uncheck (or use the 'mark resolved' action) to move this into history.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Incident Message"
        verbose_name_plural = "Incident Messages"

    def __str__(self):
        preview = self.text[:60] + "..." if len(self.text) > 60 else self.text
        return f"[{self.severity.upper()}] {preview}"
