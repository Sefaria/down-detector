"""
Health checker service using httpx with tenacity retry logic.
"""
import logging
import time
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
    follow_redirects: bool = False,
    body: dict[str, Any] | None = None,
) -> httpx.Response:
    """Make an HTTP request with the given parameters."""
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": follow_redirects,
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
            - check_type: Optional, "async_two_phase" for async verification
            - async_verification: Optional config for async polling
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
    check_type = config.get("check_type", "standard")

    if check_type == "async_two_phase":
        result = _check_async_two_phase(config)
    else:
        url = config["url"]
        method = config.get("method", "GET")
        expected_status = config.get("expected_status", 200)
        timeout = config.get("timeout", 10)
        request_body = config.get("request_body")
        follow_redirects = config.get("follow_redirects", False)

        result = _check_with_retry(
            service_name=service_name,
            url=url,
            method=method,
            expected_status=expected_status,
            timeout=timeout,
            request_body=request_body,
            follow_redirects=follow_redirects,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    if persist:
        _persist_result(result)

    return result


def _check_async_two_phase(config: dict[str, Any]) -> HealthCheckResult:
    """
    Two-phase async health check for services like the Linker API.

    Phase 1: Submit task (POST), expect 202 + task_id
    Phase 2: Poll async endpoint until task completes (SUCCESS)

    This catches failures in background workers, ML models,
    ElasticSearch, and other backend dependencies that a simple
    202 check would miss.
    """
    service_name = config["name"]
    url = config["url"]
    timeout = config.get("timeout", 15)
    request_body = config.get("request_body")
    expected_status = config.get("expected_status", 202)

    async_config = config.get("async_verification", {})
    max_poll_attempts = async_config.get("max_poll_attempts", 10)
    poll_interval = async_config.get("poll_interval", 1)
    async_base_url = async_config.get(
        "base_url", "https://www.sefaria.org/api/async/"
    )

    start_time = time.monotonic()

    try:
        # ── Phase 1: Submit task ──────────────────────────────────
        with httpx.Client() as client:
            response = _make_request(
                client=client,
                method="POST",
                url=url,
                timeout=timeout,
                body=request_body,
            )

            if response.status_code != expected_status:
                elapsed = int((time.monotonic() - start_time) * 1000)
                error = f"Phase 1 failed: expected {expected_status}, got {response.status_code}"
                logger.warning(f"Linker check failed for {service_name}: {error}")
                return HealthCheckResult(
                    service_name=service_name,
                    status="down",
                    response_time_ms=elapsed,
                    status_code=response.status_code,
                    error_message=error,
                )

            # Extract task_id from response
            try:
                data = response.json()
                task_id = data.get("task_id")
            except Exception:
                task_id = None

            if not task_id:
                elapsed = int((time.monotonic() - start_time) * 1000)
                error = "Phase 1 failed: no task_id in response"
                logger.warning(f"Linker check failed for {service_name}: {error}")
                return HealthCheckResult(
                    service_name=service_name,
                    status="down",
                    response_time_ms=elapsed,
                    status_code=response.status_code,
                    error_message=error,
                )

            logger.info(f"Linker Phase 1 passed: task_id={task_id}")

        # ── Phase 2: Poll for task completion ─────────────────────
        async_url = f"{async_base_url}{task_id}"

        with httpx.Client() as client:
            for attempt in range(max_poll_attempts):
                time.sleep(poll_interval)

                try:
                    poll_response = client.get(async_url, timeout=10)
                except (httpx.TimeoutException, httpx.HTTPError) as e:
                    logger.debug(
                        f"Linker Phase 2 poll {attempt + 1}/{max_poll_attempts} "
                        f"error: {e}"
                    )
                    continue

                if poll_response.status_code != 200:
                    logger.debug(
                        f"Linker Phase 2 poll {attempt + 1}/{max_poll_attempts}: "
                        f"status {poll_response.status_code}"
                    )
                    continue

                try:
                    result_data = poll_response.json()
                except Exception:
                    continue

                state = result_data.get("state", "")

                if state == "SUCCESS":
                    # Verify result contains actual data
                    result_content = result_data.get("result")
                    elapsed = int((time.monotonic() - start_time) * 1000)

                    if result_content:
                        logger.info(
                            f"Linker E2E check passed for {service_name} "
                            f"in {elapsed}ms (poll {attempt + 1})"
                        )
                        return HealthCheckResult(
                            service_name=service_name,
                            status="up",
                            response_time_ms=elapsed,
                            status_code=200,
                            error_message="",
                        )
                    else:
                        error = "Phase 2: task succeeded but returned empty result"
                        logger.warning(f"Linker check: {error}")
                        return HealthCheckResult(
                            service_name=service_name,
                            status="down",
                            response_time_ms=elapsed,
                            status_code=200,
                            error_message=error,
                        )

                elif state == "FAILURE":
                    elapsed = int((time.monotonic() - start_time) * 1000)
                    task_error = result_data.get("error", "Unknown error")
                    error = f"Phase 2: task failed - {task_error}"
                    logger.warning(f"Linker check failed for {service_name}: {error}")
                    return HealthCheckResult(
                        service_name=service_name,
                        status="down",
                        response_time_ms=elapsed,
                        status_code=200,
                        error_message=error,
                    )

                # PENDING or STARTED - keep polling
                logger.debug(
                    f"Linker Phase 2 poll {attempt + 1}/{max_poll_attempts}: "
                    f"state={state}"
                )

        # Polling exhausted - task never completed
        elapsed = int((time.monotonic() - start_time) * 1000)
        error = f"Phase 2: task processing timeout after {max_poll_attempts} polls"
        logger.error(f"Linker check failed for {service_name}: {error}")
        return HealthCheckResult(
            service_name=service_name,
            status="down",
            response_time_ms=elapsed,
            status_code=202,
            error_message=error,
        )

    except httpx.TimeoutException as e:
        elapsed = int((time.monotonic() - start_time) * 1000)
        error = f"Request timed out: {e}"
        logger.warning(f"Linker check timeout for {service_name}: {e}")
        return HealthCheckResult(
            service_name=service_name,
            status="down",
            response_time_ms=elapsed,
            status_code=None,
            error_message=error,
        )

    except httpx.ConnectError as e:
        elapsed = int((time.monotonic() - start_time) * 1000)
        error = f"Connection error: {e}"
        logger.warning(f"Linker check connection error for {service_name}: {e}")
        return HealthCheckResult(
            service_name=service_name,
            status="down",
            response_time_ms=elapsed,
            status_code=None,
            error_message=error,
        )

    except Exception as e:
        elapsed = int((time.monotonic() - start_time) * 1000)
        error = f"Unexpected error: {e}"
        logger.exception(f"Unexpected error checking Linker {service_name}")
        return HealthCheckResult(
            service_name=service_name,
            status="down",
            response_time_ms=elapsed,
            status_code=None,
            error_message=error,
        )


def _check_with_retry(
    service_name: str,
    url: str,
    method: str,
    expected_status: int,
    timeout: int,
    request_body: dict[str, Any] | None,
    follow_redirects: bool,
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
                    follow_redirects=follow_redirects,
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
