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


def send_alert(result: "HealthCheckResult", transition: str) -> bool:
    """
    Send a Slack alert for a state transition.
    
    Args:
        result: The health check result that triggered the alert
        transition: Either "went_down" or "recovered"
        
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
            blocks = _build_recovery_alert(result)
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


def _build_down_alert(result: "HealthCheckResult") -> list[dict]:
    """Build Block Kit blocks for a service down alert."""
    status_page_url = getattr(settings, "STATUS_PAGE_URL", "https://status.sefaria.org")
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    
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


def _build_recovery_alert(result: "HealthCheckResult") -> list[dict]:
    """Build Block Kit blocks for a service recovery alert."""
    status_page_url = getattr(settings, "STATUS_PAGE_URL", "https://status.sefaria.org")
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    
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
    transitions: list[tuple["HealthCheckResult", str]]
) -> int:
    """
    Process a list of transitions and send alerts for each.
    
    Args:
        transitions: List of (result, transition_type) tuples
        
    Returns:
        Number of alerts successfully sent
    """
    alerts_sent = 0
    
    for result, transition in transitions:
        if send_alert(result, transition):
            alerts_sent += 1
    
    return alerts_sent
