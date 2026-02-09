"""
APScheduler setup for periodic health checks and cleanup.

This module configures APScheduler to:
1. Run health checks on all monitored services at regular intervals
2. Clean up old health check records daily
"""
import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.utils import timezone

from monitoring.models import HealthCheck
from monitoring.services.checker import check_all_services
from monitoring.services.state import get_state_tracker
from monitoring.services.alerter import process_transitions_with_alerts

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: BackgroundScheduler | None = None


def run_health_check_cycle():
    """
    Run a complete health check cycle.
    
    This function:
    1. Checks all configured services
    2. Persists results to the database
    3. Processes state transitions
    4. Sends Slack alerts for any transitions
    """
    logger.info("Starting health check cycle...")
    
    try:
        # 1. Get state tracker first (ensures it reads PREVIOUS state from DB)
        tracker = get_state_tracker()
        
        # 2. Run checks and persist results
        results = check_all_services(persist=True)
        
        # 3. Process results relative to the tracker's initialized state
        transitions = tracker.process_results(results)
        
        # Send Slack alerts for transitions
        if transitions:
            alerts_sent = process_transitions_with_alerts(transitions)
            logger.info(f"Sent {alerts_sent} Slack alerts")
        
        # Summary log
        up_count = sum(1 for r in results if r.is_up)
        down_count = len(results) - up_count
        logger.info(
            f"Health check cycle complete: {up_count} up, {down_count} down, "
            f"{len(transitions)} transitions"
        )
        
    except Exception as e:
        logger.exception(f"Error in health check cycle: {e}")


def run_cleanup_job():
    """
    Clean up old health check records.
    
    Runs daily to prevent the database from growing indefinitely.
    Uses the HEALTH_CHECK_RETENTION_DAYS setting (default: 30 days).
    """
    logger.info("Starting cleanup job...")
    
    try:
        retention_days = getattr(settings, "HEALTH_CHECK_RETENTION_DAYS", 30)
        cutoff_date = timezone.now() - timedelta(days=retention_days)
        
        old_checks = HealthCheck.objects.filter(checked_at__lt=cutoff_date)
        count = old_checks.count()
        
        if count > 0:
            deleted_count, _ = old_checks.delete()
            logger.info(
                f"Cleanup complete: deleted {deleted_count} records "
                f"older than {retention_days} days"
            )
        else:
            logger.info("Cleanup complete: no old records to delete")
            
    except Exception as e:
        logger.exception(f"Error in cleanup job: {e}")


def get_scheduler() -> BackgroundScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler


def start_scheduler():
    """
    Start the scheduler with health check and cleanup jobs.
    
    Jobs:
    - Health check: runs every HEALTH_CHECK_INTERVAL seconds (default: 60)
    - Cleanup: runs daily at 3:00 AM UTC
    """
    scheduler = get_scheduler()
    
    # Get interval from settings
    interval_seconds = getattr(settings, "HEALTH_CHECK_INTERVAL", 60)
    
    # Job 1: Health checks (runs every N seconds)
    scheduler.add_job(
        run_health_check_cycle,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id="health_check_cycle",
        name="Periodic Health Check",
        replace_existing=True,
        max_instances=1,  # Prevent overlapping executions
    )
    logger.info(f"Health check job configured: every {interval_seconds}s")
    
    # Job 2: Cleanup (runs daily at 3:00 AM UTC)
    scheduler.add_job(
        run_cleanup_job,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id="daily_cleanup",
        name="Daily Cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,  # If missed, only run once when back online
    )
    logger.info("Cleanup job configured: daily at 3:00 AM UTC")
    
    # Start the scheduler
    scheduler.start()
    logger.info("Scheduler started with 2 jobs")
    
    return scheduler


def stop_scheduler():
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("Scheduler stopped")
