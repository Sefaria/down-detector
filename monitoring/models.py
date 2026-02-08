"""
Models for the Sefaria status monitoring system.
"""
from django.db import models


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


class Message(models.Model):
    """
    An incident message for the status page.
    """

    SEVERITY_CHOICES = [
        ("high", "High"),
        ("medium", "Medium"),
        ("resolved", "Resolved"),
    ]

    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    text = models.TextField()
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Incident Message"
        verbose_name_plural = "Incident Messages"

    def __str__(self):
        preview = self.text[:60] + "..." if len(self.text) > 60 else self.text
        return f"[{self.severity.upper()}] {preview}"
