"""
Tests for the scheduler service.
"""
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.django_db


class TestScheduler:
    """Tests for the scheduler module."""

    @patch("monitoring.services.scheduler.check_all_services")
    @patch("monitoring.services.scheduler.get_state_tracker")
    def test_run_health_check_cycle(self, mock_get_tracker, mock_check_all):
        """run_health_check_cycle calls check_all_services and processes results."""
        from monitoring.services.scheduler import run_health_check_cycle
        from monitoring.services.checker import HealthCheckResult
        
        # Mock the checker to return some results
        mock_results = [
            HealthCheckResult(
                service_name="test-service",
                status="up",
                response_time_ms=100,
                status_code=200,
                error_message="",
            )
        ]
        mock_check_all.return_value = mock_results
        
        # Mock the state tracker
        mock_tracker = MagicMock()
        mock_tracker.process_results.return_value = []
        mock_get_tracker.return_value = mock_tracker
        
        # Run the cycle
        run_health_check_cycle()
        
        # Verify check_all_services was called
        mock_check_all.assert_called_once_with(persist=True)
        
        # Verify state tracker processed results
        mock_tracker.process_results.assert_called_once_with(mock_results)

    @patch("monitoring.services.scheduler.check_all_services")
    @patch("monitoring.services.scheduler.get_state_tracker")
    def test_run_health_check_cycle_handles_transitions(
        self, mock_get_tracker, mock_check_all
    ):
        """run_health_check_cycle logs transitions correctly."""
        from monitoring.services.scheduler import run_health_check_cycle
        from monitoring.services.checker import HealthCheckResult
        
        down_result = HealthCheckResult(
            service_name="failing-service",
            status="down",
            response_time_ms=None,
            status_code=503,
            error_message="Service Unavailable",
        )
        mock_check_all.return_value = [down_result]
        
        mock_tracker = MagicMock()
        mock_tracker.process_results.return_value = [(down_result, "went_down")]
        mock_get_tracker.return_value = mock_tracker
        
        # Should not raise
        run_health_check_cycle()
        
        mock_tracker.process_results.assert_called_once()


class TestSchedulerStartStop:
    """Tests for scheduler start/stop functions."""

    def test_get_scheduler_returns_scheduler(self):
        """get_scheduler returns a BackgroundScheduler instance."""
        from monitoring.services.scheduler import get_scheduler, _scheduler
        import monitoring.services.scheduler as scheduler_module
        
        # Reset global state
        scheduler_module._scheduler = None
        
        scheduler = get_scheduler()
        
        from apscheduler.schedulers.background import BackgroundScheduler
        assert isinstance(scheduler, BackgroundScheduler)
        
        # Cleanup
        scheduler_module._scheduler = None

    @patch("monitoring.services.scheduler.BackgroundScheduler")
    def test_start_scheduler_configures_job(self, mock_scheduler_class):
        """start_scheduler adds health check and cleanup jobs."""
        from monitoring.services.scheduler import start_scheduler
        import monitoring.services.scheduler as scheduler_module
        
        # Reset global state
        scheduler_module._scheduler = None
        
        mock_scheduler = MagicMock()
        mock_scheduler_class.return_value = mock_scheduler
        
        start_scheduler()
        
        # Verify both jobs were added (health check + cleanup)
        assert mock_scheduler.add_job.call_count == 2
        
        # Verify health check job was configured
        call_args_list = mock_scheduler.add_job.call_args_list
        job_ids = [call[1]["id"] for call in call_args_list]
        assert "health_check_cycle" in job_ids
        assert "daily_cleanup" in job_ids
        
        # Verify scheduler was started
        mock_scheduler.start.assert_called_once()
        
        # Cleanup
        scheduler_module._scheduler = None
