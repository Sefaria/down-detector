"""
Slack alerter service for sending notifications on state transitions.

Uses the Slack SDK's WebhookClient to send rich Block Kit messages
when services go down or recover.
"""
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from django.conf import settings
from django.utils import timezone
from slack_sdk.webhook import WebhookClient

if TYPE_CHECKING:
    from monitoring.services.checker import HealthCheckResult

logger = logging.getLogger(__name__)


def send_alert(
    result: "HealthCheckResult",
    transition: str,
    outage_start: datetime | None = None,
) -> bool:
    """
    Send a Slack alert for a state transition.
    
    Args:
        result: The health check result that triggered the alert
        transition: Either "went_down" or "recovered"
        outage_start: Optional precise time the outage began (for recoveries)
        
    Returns:
        True if alert was sent successfully, False otherwise
    """
    webhook_url = getattr(settings, "SLACK_WEBHOOK_URL", "")
    
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL not configured, skipping alert")
        return False
    
    try:
        client = WebhookClient(webhook_url)
        
        if transition == "went_down":
            blocks = _build_down_alert(result)
            text = f"🔴 Service Down: {result.service_name}"
        elif transition == "recovered":
            blocks = _build_recovery_alert(result, outage_start)
            text = f"🟢 Service Recovered: {result.service_name}"
        else:
            logger.error(f"Unknown transition type: {transition}")
            return False
        
        response = client.send(text=text, blocks=blocks)
        
        if response.status_code == 200:
            logger.info(f"Slack alert sent for {result.service_name}: {transition}")
            return True
        else:
            logger.error(
                f"Slack alert failed: {response.status_code} - {response.body}"
            )
            return False
            
    except Exception as e:
        logger.exception(f"Error sending Slack alert: {e}")
        return False


def _get_outage_start_time(service_name: str) -> str:
    """
    Find when the current outage started by querying the DB for the
    first consecutive 'down' record after the last 'up' record.

    Returns a formatted timestamp string.
    """
    from monitoring.models import HealthCheck

    last_up = (
        HealthCheck.objects
        .filter(service_name=service_name, status="up")
        .order_by("-checked_at")
        .first()
    )

    if last_up:
        first_down = (
            HealthCheck.objects
            .filter(
                service_name=service_name,
                status="down",
                checked_at__gt=last_up.checked_at,
            )
            .order_by("checked_at")
            .first()
        )
        if first_down:
            return first_down.checked_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        # No 'up' record at all — use the earliest 'down'
        earliest_down = (
            HealthCheck.objects
            .filter(service_name=service_name, status="down")
            .order_by("checked_at")
            .first()
        )
        if earliest_down:
            return earliest_down.checked_at.strftime("%Y-%m-%d %H:%M:%S UTC")

    return timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")


def _build_down_alert(result: "HealthCheckResult") -> list[dict]:
    """Build Block Kit blocks for a service down alert."""
    status_page_url = getattr(settings, "STATUS_PAGE_URL", "https://status.sefaria.org")
    timestamp = _get_outage_start_time(result.service_name)
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🔴 Service Down: {result.service_name}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Status:*\nDOWN",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Since:*\n{timestamp}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*HTTP Code:*\n{result.status_code or 'N/A'}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Error:*\n{result.error_message[:100] or 'N/A'}",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{status_page_url}|View Status Page>",
                },
            ],
        },
    ]
    
    return blocks


def _get_downtime_duration(
    service_name: str, known_outage_start: datetime | None = None
) -> str:
    """
    Calculate how long a service was down. Uses the StateTracker's documented
    outage start time if available. Falls back to querying the DB on startup.

    Returns a human-readable duration string like '2h 15m' or '45m 30s'.
    """
    if known_outage_start:
        outage_start = known_outage_start
    else:
        from monitoring.models import HealthCheck

        # Get the most recent "down" record for this service
        last_down = (
            HealthCheck.objects
            .filter(service_name=service_name, status="down")
            .order_by("-checked_at")
            .first()
        )

        if not last_down:
            return "Unknown"

        # Find the last "up" record BEFORE the most recent down record.
        # This tells us when the outage actually started.
        last_up_before_outage = (
            HealthCheck.objects
            .filter(
                service_name=service_name,
                status="up",
                checked_at__lt=last_down.checked_at,
            )
            .order_by("-checked_at")
            .first()
        )

        if last_up_before_outage:
            # The outage started with the first "down" record after the last "up"
            first_down = (
                HealthCheck.objects
                .filter(
                    service_name=service_name,
                    status="down",
                    checked_at__gt=last_up_before_outage.checked_at,
                )
                .order_by("checked_at")
                .first()
            )
            outage_start = first_down.checked_at if first_down else last_down.checked_at
        else:
            # No "up" record found before the outage — use the earliest "down"
            earliest_down = (
                HealthCheck.objects
                .filter(service_name=service_name, status="down")
                .order_by("checked_at")
                .first()
            )
            outage_start = earliest_down.checked_at if earliest_down else last_down.checked_at

    now = timezone.now()
    duration = now - outage_start

    total_seconds = int(duration.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"


def _build_recovery_alert(
    result: "HealthCheckResult", outage_start: datetime | None = None
) -> list[dict]:
    """Build Block Kit blocks for a service recovery alert."""
    status_page_url = getattr(settings, "STATUS_PAGE_URL", "https://status.sefaria.org")
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    downtime = _get_downtime_duration(result.service_name, outage_start)
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🟢 Service Recovered: {result.service_name}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Status:*\nUP",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Recovered:*\n{timestamp}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Downtime:*\n{downtime}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Response Time:*\n{result.response_time_ms or 'N/A'}ms",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{status_page_url}|View Status Page>",
                },
            ],
        },
    ]
    
    return blocks


def process_transitions_with_alerts(
    transitions: list[tuple["HealthCheckResult", str, datetime | None]]
) -> int:
    """
    Process a list of transitions and send alerts for each.
    
    Args:
        transitions: List of (result, transition_type, outage_start) tuples
        
    Returns:
        Number of alerts successfully sent
    """
    alerts_sent = 0
    
    for result, transition, outage_start in transitions:
        if send_alert(result, transition, outage_start):
            alerts_sent += 1
    
    return alerts_sent
