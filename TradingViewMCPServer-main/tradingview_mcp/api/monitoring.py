"""Thread-safe runtime and API endpoint health metrics."""

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApiHealthMonitor:
    """Track process uptime and outcomes of requests to an external API."""

    def __init__(self) -> None:
        self._started_at = _utc_now()
        self._started_monotonic = time.monotonic()
        self._lock = threading.Lock()
        self._requests_total = 0
        self._requests_succeeded = 0
        self._requests_failed = 0
        self._api_errors = 0
        self._local_rate_limits = 0
        self._latency_total_ms = 0.0
        self._last_request_at: Optional[str] = None
        self._last_request_succeeded: Optional[bool] = None
        self._last_request_error: Optional[str] = None
        self._endpoint_status = "unknown"
        self._last_checked_at: Optional[str] = None
        self._last_response_time_ms: Optional[float] = None
        self._consecutive_endpoint_failures = 0
        self._last_error: Optional[str] = None

    def record_request(
        self,
        endpoint_reachable: Optional[bool],
        duration_ms: float,
        succeeded: bool,
        api_error: bool = False,
        error: Optional[str] = None,
    ) -> None:
        """Record one outbound request or a local rejection before it was sent."""
        if not math.isfinite(duration_ms) or duration_ms < 0:
            raise ValueError("duration_ms must be a finite, non-negative number")
        if endpoint_reachable is None and (succeeded or api_error):
            raise ValueError("a local rejection cannot succeed or contain an API error")

        checked_at = _utc_now()
        with self._lock:
            self._last_request_at = checked_at
            if endpoint_reachable is None:
                self._local_rate_limits += 1
                self._last_request_succeeded = False
                self._last_request_error = error or "locally_rejected"
                return

            self._requests_total += 1
            self._latency_total_ms += duration_ms
            self._last_request_succeeded = succeeded
            self._last_request_error = (
                None
                if succeeded
                else error or ("api_error" if api_error else "request_failed")
            )
            if succeeded:
                self._requests_succeeded += 1
            else:
                self._requests_failed += 1
            if api_error:
                self._api_errors += 1

            self._last_checked_at = checked_at
            self._last_response_time_ms = round(duration_ms, 2)
            if endpoint_reachable:
                self._endpoint_status = "healthy"
                self._consecutive_endpoint_failures = 0
                self._last_error = None
            else:
                self._endpoint_status = "unhealthy"
                self._consecutive_endpoint_failures += 1
                self._last_error = error or "endpoint_unreachable"

    def get_stats(self, endpoint_url: str) -> Dict[str, Any]:
        """Return a consistent snapshot of current health and request metrics."""
        with self._lock:
            total = self._requests_total
            success_rate = (
                self._requests_succeeded / total * 100 if total else 0.0
            )
            average_latency = self._latency_total_ms / total if total else 0.0
            return {
                "uptime_seconds": round(
                    time.monotonic() - self._started_monotonic, 2
                ),
                "started_at": self._started_at,
                "api_endpoint": {
                    "url": endpoint_url,
                    "status": self._endpoint_status,
                    "last_checked_at": self._last_checked_at,
                    "last_response_time_ms": self._last_response_time_ms,
                    "consecutive_failures": self._consecutive_endpoint_failures,
                    "last_error": self._last_error,
                },
                "requests": {
                    "total": total,
                    "succeeded": self._requests_succeeded,
                    "failed": self._requests_failed,
                    "api_errors": self._api_errors,
                    "local_rate_limits": self._local_rate_limits,
                    "success_rate": round(success_rate, 2),
                    "average_response_time_ms": round(average_latency, 2),
                    "last_request_at": self._last_request_at,
                    "last_succeeded": self._last_request_succeeded,
                    "last_error": self._last_request_error,
                },
            }
