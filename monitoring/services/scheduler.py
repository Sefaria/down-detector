"""
APScheduler setup for periodic health checks.

This module configures APScheduler to run health checks on
all monitored services at regular intervals.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.conf import settings

from monitoring.services.checker import check_all_services
from monitoring.services.state import get_state_tracker

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
    4. Triggers alerts for any transitions (TODO: Phase 2)
    """
    logger.info("Starting health check cycle...")
    
    try:
        # Run checks and persist results
        results = check_all_services(persist=True)
        
        # Get state tracker and process results
        tracker = get_state_tracker()
        transitions = tracker.process_results(results)
        
        # Log transitions (alerts will be added in Phase 2)
        for result, transition in transitions:
            if transition == "went_down":
                logger.warning(
                    f"ALERT: {result.service_name} went DOWN "
                    f"(status_code={result.status_code}, error={result.error_message})"
                )
            elif transition == "recovered":
                logger.info(
                    f"RECOVERY: {result.service_name} is back UP "
                    f"(response_time={result.response_time_ms}ms)"
                )
        
        # Summary log
        up_count = sum(1 for r in results if r.is_up)
        down_count = len(results) - up_count
        logger.info(
            f"Health check cycle complete: {up_count} up, {down_count} down, "
            f"{len(transitions)} transitions"
        )
        
    except Exception as e:
        logger.exception(f"Error in health check cycle: {e}")


def get_scheduler() -> BackgroundScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler


def start_scheduler():
    """
    Start the scheduler with the health check job.
    
    This should be called from the management command or
    when starting the scheduler in a separate process.
    """
    scheduler = get_scheduler()
    
    # Get interval from settings
    interval_seconds = getattr(settings, "HEALTH_CHECK_INTERVAL", 60)
    
    # Add the health check job
    scheduler.add_job(
        run_health_check_cycle,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id="health_check_cycle",
        name="Periodic Health Check",
        replace_existing=True,
    )
    
    logger.info(f"Scheduler configured with {interval_seconds}s interval")
    
    # Start the scheduler
    scheduler.start()
    logger.info("Scheduler started")
    
    return scheduler


def stop_scheduler():
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        logger.info("Scheduler stopped")
