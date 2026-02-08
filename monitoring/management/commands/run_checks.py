"""
Management command to run the health check scheduler.

Usage:
    python manage.py run_checks

This starts the APScheduler-based health check loop that runs
indefinitely until interrupted (Ctrl+C).
"""
import signal
import sys
import time

from django.core.management.base import BaseCommand

from monitoring.services.scheduler import start_scheduler, stop_scheduler


class Command(BaseCommand):
    help = "Run the health check scheduler"

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run health checks once and exit (for testing)",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting health check scheduler..."))

        if options["once"]:
            # Run once for testing
            from monitoring.services.scheduler import run_health_check_cycle
            run_health_check_cycle()
            self.stdout.write(self.style.SUCCESS("Health check cycle complete."))
            return

        # Set up signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            self.stdout.write("\nShutting down scheduler...")
            stop_scheduler()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start the scheduler
        scheduler = start_scheduler()

        self.stdout.write(
            self.style.SUCCESS("Scheduler running. Press Ctrl+C to stop.")
        )

        # Keep the main thread alive
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            self.stdout.write("\nShutting down scheduler...")
            stop_scheduler()
