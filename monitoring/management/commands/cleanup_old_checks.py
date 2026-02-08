"""
Management command to clean up old health check records.

Usage:
    python manage.py cleanup_old_checks
    python manage.py cleanup_old_checks --dry-run
    python manage.py cleanup_old_checks --days 14

This removes health check records older than the retention period
to prevent the database from growing indefinitely.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from monitoring.models import HealthCheck


class Command(BaseCommand):
    help = "Delete health check records older than retention period"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Retention period in days (overrides settings)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        # Determine retention period
        retention_days = options["days"]
        if retention_days is None:
            retention_days = getattr(settings, "HEALTH_CHECK_RETENTION_DAYS", 30)

        dry_run = options["dry_run"]
        
        # Calculate cutoff date
        cutoff_date = timezone.now() - timedelta(days=retention_days)
        
        # Find old records
        old_checks = HealthCheck.objects.filter(checked_at__lt=cutoff_date)
        count = old_checks.count()

        if dry_run:
            self.stdout.write(
                f"[DRY RUN] Would delete {count} health check records "
                f"older than {retention_days} days"
            )
        else:
            # Delete old records
            deleted_count, _ = old_checks.delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {deleted_count} health check records "
                    f"older than {retention_days} days"
                )
            )
