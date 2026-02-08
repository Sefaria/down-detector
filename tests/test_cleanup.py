"""
Tests for the cleanup_old_checks management command.
"""
import pytest
from django.utils import timezone
from datetime import timedelta

from tests.factories import HealthCheckFactory


pytestmark = pytest.mark.django_db


class TestCleanupOldChecks:
    """Tests for the cleanup_old_checks management command."""

    def test_deletes_old_health_checks(self, settings):
        """Deletes HealthCheck records older than retention period."""
        from django.core.management import call_command
        from monitoring.models import HealthCheck
        
        settings.HEALTH_CHECK_RETENTION_DAYS = 7
        
        # Create old and new health checks
        now = timezone.now()
        old_check = HealthCheckFactory(
            checked_at=now - timedelta(days=10),
        )
        new_check = HealthCheckFactory(
            checked_at=now - timedelta(days=1),
        )
        
        # Run cleanup
        call_command("cleanup_old_checks")
        
        # Old check should be deleted, new should remain
        assert not HealthCheck.objects.filter(pk=old_check.pk).exists()
        assert HealthCheck.objects.filter(pk=new_check.pk).exists()

    def test_respects_retention_days_setting(self, settings):
        """Uses HEALTH_CHECK_RETENTION_DAYS from settings."""
        from django.core.management import call_command
        from monitoring.models import HealthCheck
        
        settings.HEALTH_CHECK_RETENTION_DAYS = 30
        
        now = timezone.now()
        # 15 days old - should survive with 30-day retention
        check = HealthCheckFactory(
            checked_at=now - timedelta(days=15),
        )
        
        call_command("cleanup_old_checks")
        
        assert HealthCheck.objects.filter(pk=check.pk).exists()

    def test_dry_run_does_not_delete(self, settings):
        """Dry run shows what would be deleted without deleting."""
        from django.core.management import call_command
        from monitoring.models import HealthCheck
        
        settings.HEALTH_CHECK_RETENTION_DAYS = 7
        
        now = timezone.now()
        old_check = HealthCheckFactory(
            checked_at=now - timedelta(days=10),
        )
        
        # Run with --dry-run
        call_command("cleanup_old_checks", dry_run=True)
        
        # Should still exist
        assert HealthCheck.objects.filter(pk=old_check.pk).exists()

    def test_reports_deleted_count(self, settings, capsys):
        """Command reports how many records were deleted."""
        from django.core.management import call_command
        
        settings.HEALTH_CHECK_RETENTION_DAYS = 7
        
        now = timezone.now()
        # Create 3 old checks
        for _ in range(3):
            HealthCheckFactory(checked_at=now - timedelta(days=10))
        
        call_command("cleanup_old_checks")
        
        captured = capsys.readouterr()
        assert "3" in captured.out or "Deleted" in captured.out
