"""Tests for process and API health monitoring."""

import pytest
import requests

from tradingview_mcp.api import AlphaVantageClient, ApiHealthMonitor


def test_monitor_starts_with_unknown_endpoint():
    stats = ApiHealthMonitor().get_stats("https://example.test/query")

    assert stats["uptime_seconds"] >= 0
    assert stats["api_endpoint"]["status"] == "unknown"
    assert stats["api_endpoint"]["last_checked_at"] is None
    assert stats["requests"]["total"] == 0
    assert stats["requests"]["success_rate"] == 0


def test_monitor_tracks_request_metrics_and_endpoint_recovery():
    monitor = ApiHealthMonitor()
    monitor.record_request(False, 100, succeeded=False, error="network_error")
    monitor.record_request(False, 200, succeeded=False, error="network_error")
    monitor.record_request(True, 50, succeeded=True)

    stats = monitor.get_stats("https://example.test/query")

    assert stats["api_endpoint"]["status"] == "healthy"
    assert stats["api_endpoint"]["consecutive_failures"] == 0
    assert stats["api_endpoint"]["last_error"] is None
    assert stats["requests"]["total"] == 3
    assert stats["requests"]["succeeded"] == 1
    assert stats["requests"]["failed"] == 2
    assert stats["requests"]["success_rate"] == pytest.approx(33.33, abs=0.01)
    assert stats["requests"]["average_response_time_ms"] == pytest.approx(116.67)


def test_monitor_counts_api_errors_without_marking_endpoint_unhealthy():
    monitor = ApiHealthMonitor()
    monitor.record_request(
        True, 12, succeeded=False, api_error=True, error="api_rate_limit"
    )

    stats = monitor.get_stats("https://example.test/query")

    assert stats["api_endpoint"]["status"] == "healthy"
    assert stats["requests"]["api_errors"] == 1
    assert stats["requests"]["failed"] == 1


def test_monitor_rejects_invalid_duration():
    monitor = ApiHealthMonitor()

    with pytest.raises(ValueError, match="duration_ms"):
        monitor.record_request(True, -1, succeeded=False)


def test_api_client_tracks_successful_endpoint_request(monkeypatch):
    client = AlphaVantageClient(api_key="test-key")
    payload = {"Global Quote": {"05. price": "100.00"}}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(
        "tradingview_mcp.api.alpha_vantage.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    assert client._make_request({"function": "GLOBAL_QUOTE"}) == payload

    stats = client.get_monitoring_stats()
    assert stats["api_endpoint"]["status"] == "healthy"
    assert stats["requests"]["total"] == 1
    assert stats["requests"]["succeeded"] == 1
    assert client._total_calls == 1


def test_api_client_tracks_network_failure(monkeypatch):
    client = AlphaVantageClient(api_key="test-key")

    def timeout(*args, **kwargs):
        raise requests.Timeout("request timed out")

    monkeypatch.setattr("tradingview_mcp.api.alpha_vantage.requests.get", timeout)

    result = client._make_request({"function": "GLOBAL_QUOTE"})

    stats = client.get_monitoring_stats()
    assert result["error"] == "Request timeout"
    assert stats["api_endpoint"]["status"] == "unhealthy"
    assert stats["api_endpoint"]["consecutive_failures"] == 1
    assert stats["api_endpoint"]["last_error"] == "request_timeout"
    assert stats["requests"]["failed"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"Note": "API rate limit reached"},
        {"Information": "Invalid API key"},
    ],
)
def test_api_client_tracks_api_errors_without_marking_endpoint_unhealthy(
    monkeypatch, payload
):
    client = AlphaVantageClient(api_key="test-key")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(
        "tradingview_mcp.api.alpha_vantage.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    result = client._make_request({"function": "GLOBAL_QUOTE"})
    stats = client.get_monitoring_stats()

    assert result["error"]
    assert stats["api_endpoint"]["status"] == "healthy"
    assert stats["requests"]["api_errors"] == 1
    assert stats["requests"]["failed"] == 1


def test_local_rate_limit_does_not_change_endpoint_health(monkeypatch):
    client = AlphaVantageClient(api_key="test-key")
    monkeypatch.setattr(client.rate_limiter, "can_proceed", lambda: False)
    monkeypatch.setattr(client.rate_limiter, "wait_time", lambda: 10)

    result = client._make_request({"function": "GLOBAL_QUOTE"})

    stats = client.get_monitoring_stats()
    assert result["error"]
    assert stats["api_endpoint"]["status"] == "unknown"
    assert stats["requests"]["total"] == 0
    assert stats["requests"]["local_rate_limits"] == 1
    assert stats["requests"]["last_succeeded"] is False
    assert stats["requests"]["last_error"] == "local_rate_limit"


def test_server_error_marks_endpoint_unhealthy(monkeypatch):
    client = AlphaVantageClient(api_key="test-key")

    class FakeResponse:
        status_code = 503

        def raise_for_status(self):
            raise requests.HTTPError("service unavailable", response=self)

    monkeypatch.setattr(
        "tradingview_mcp.api.alpha_vantage.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )

    client._make_request({"function": "GLOBAL_QUOTE"})
    stats = client.get_monitoring_stats()

    assert stats["api_endpoint"]["status"] == "unhealthy"
    assert stats["requests"]["last_succeeded"] is False
    assert stats["requests"]["last_error"] == "http_error"
