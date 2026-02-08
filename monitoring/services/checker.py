"""
Health checker service using httpx with tenacity retry logic.
"""
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings
from django.utils import timezone
from tenacity import (
    retry,
    stop_after_attempt,
    wait_fixed,
    retry_if_exception_type,
)

from monitoring.models import HealthCheck

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    service_name: str
    status: str  # "up" or "down"
    response_time_ms: int | None
    status_code: int | None
    error_message: str

    @property
    def is_up(self) -> bool:
        """Returns True if the service is up."""
        return self.status == "up"


def _make_request(
    client: httpx.Client,
    method: str,
    url: str,
    timeout: int,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    """Make an HTTP request with the given parameters."""
    kwargs: dict[str, Any] = {
        "timeout": timeout,
    }
    if body and method.upper() == "POST":
        kwargs["json"] = body

    return client.request(method, url, **kwargs)


def check_service(
    config: dict[str, Any],
    max_retries: int | None = None,
    retry_delay: float | None = None,
    persist: bool = False,
) -> HealthCheckResult:
    """
    Check the health of a service.

    Args:
        config: Service configuration dict with keys:
            - name: Service name
            - url: Health check URL
            - method: HTTP method (GET, POST)
            - expected_status: Expected HTTP status code
            - timeout: Request timeout in seconds
            - request_body: Optional request body for POST
        max_retries: Number of retry attempts (default from settings)
        retry_delay: Delay between retries in seconds (default from settings)
        persist: Whether to save result to database

    Returns:
        HealthCheckResult with status and diagnostic info
    """
    if max_retries is None:
        max_retries = getattr(settings, "HEALTH_CHECK_RETRIES", 3)
    if retry_delay is None:
        retry_delay = getattr(settings, "HEALTH_CHECK_RETRY_DELAY", 10)

    service_name = config["name"]
    url = config["url"]
    method = config.get("method", "GET")
    expected_status = config.get("expected_status", 200)
    timeout = config.get("timeout", 10)
    request_body = config.get("request_body")

    result = _check_with_retry(
        service_name=service_name,
        url=url,
        method=method,
        expected_status=expected_status,
        timeout=timeout,
        request_body=request_body,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )

    if persist:
        _persist_result(result)

    return result


def _check_with_retry(
    service_name: str,
    url: str,
    method: str,
    expected_status: int,
    timeout: int,
    request_body: dict[str, Any] | None,
    max_retries: int,
    retry_delay: float,
) -> HealthCheckResult:
    """Perform health check with retry logic."""
    last_error: str = ""
    last_status_code: int | None = None
    response_time_ms: int | None = None

    for attempt in range(max_retries):
        try:
            with httpx.Client() as client:
                response = _make_request(
                    client=client,
                    method=method,
                    url=url,
                    timeout=timeout,
                    body=request_body,
                )

                response_time_ms = int(response.elapsed.total_seconds() * 1000)
                last_status_code = response.status_code

                if response.status_code == expected_status:
                    logger.info(
                        f"Health check passed for {service_name}: "
                        f"{response.status_code} in {response_time_ms}ms"
                    )
                    return HealthCheckResult(
                        service_name=service_name,
                        status="up",
                        response_time_ms=response_time_ms,
                        status_code=response.status_code,
                        error_message="",
                    )
                else:
                    last_error = f"Expected {expected_status}, got {response.status_code}"
                    logger.warning(
                        f"Health check failed for {service_name}: {last_error}"
                    )

        except httpx.TimeoutException as e:
            last_error = f"Request timed out: {e}"
            logger.warning(f"Health check timeout for {service_name}: {e}")

        except httpx.ConnectError as e:
            last_error = f"Connection error: {e}"
            logger.warning(f"Health check connection error for {service_name}: {e}")

        except httpx.HTTPError as e:
            last_error = f"HTTP error: {e}"
            logger.warning(f"Health check HTTP error for {service_name}: {e}")

        except Exception as e:
            last_error = f"Unexpected error: {e}"
            logger.exception(f"Unexpected error checking {service_name}")

        # Wait before retry (except on last attempt)
        if attempt < max_retries - 1:
            import time
            time.sleep(retry_delay)

    # All retries failed
    logger.error(f"Health check failed for {service_name} after {max_retries} attempts")
    return HealthCheckResult(
        service_name=service_name,
        status="down",
        response_time_ms=response_time_ms,
        status_code=last_status_code,
        error_message=last_error,
    )


def _persist_result(result: HealthCheckResult) -> HealthCheck:
    """Save health check result to database."""
    return HealthCheck.objects.create(
        service_name=result.service_name,
        status=result.status,
        response_time_ms=result.response_time_ms,
        status_code=result.status_code,
        error_message=result.error_message,
        checked_at=timezone.now(),
    )


def check_all_services(persist: bool = True) -> list[HealthCheckResult]:
    """
    Check health of all configured services.

    Returns:
        List of HealthCheckResult for each service
    """
    services = getattr(settings, "MONITORED_SERVICES", [])
    results = []

    for config in services:
        result = check_service(config, persist=persist)
        results.append(result)

    return results
