import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(scripts_dir))

from monitor_backends import (
    DEFAULT_TARGETS,
    EndpointConfig,
    ProbeResult,
    ServiceTarget,
    probe_endpoint,
    run_monitor,
    validate_safe_url,
)


class TestSafeUrlValidation:
    def test_valid_https_urls(self):
        validate_safe_url("https://search-sphere-api.onrender.com")
        validate_safe_url("https://quickclinic-socket-server.onrender.com/health")
        validate_safe_url("https://exam-arena-mu.vercel.app/api/health")

    def test_reject_insecure_http(self):
        with pytest.raises(ValueError, match="only https:// is allowed"):
            validate_safe_url("http://search-sphere-api.onrender.com")

    def test_reject_localhost_and_loopback(self):
        with pytest.raises(ValueError, match="Prohibited host"):
            validate_safe_url("https://localhost/health")
        with pytest.raises(ValueError, match="Prohibited host"):
            validate_safe_url("https://127.0.0.1:8000/health")

    def test_reject_private_network_ips(self):
        with pytest.raises(ValueError, match="private network access blocked"):
            validate_safe_url("https://10.0.0.1/ready")
        with pytest.raises(ValueError, match="private network access blocked"):
            validate_safe_url("https://192.168.1.100/health")
        with pytest.raises(ValueError, match="private network access blocked"):
            validate_safe_url("https://172.16.5.4/health")

    def test_reject_missing_hostname(self):
        with pytest.raises(ValueError, match="Missing hostname"):
            validate_safe_url("https:///health")


class TestEndpointProbing:
    @patch("urllib.request.urlopen")
    def test_successful_probe(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = b'{"status": "ok", "service": "test"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        config = EndpointConfig(path="/health", expected_status=200, status_field="status", allowed_status_values=("ok",))
        result = probe_endpoint("Test Service", "https://api.example.com/health", config, max_retries=1)

        assert result.success is True
        assert result.status_code == 200
        assert result.attempts == 1
        assert result.is_cold_start is False
        assert result.data == {"status": "ok", "service": "test"}

    @patch("urllib.request.urlopen")
    def test_cold_start_retry_recovery(self, mock_urlopen):
        # First attempt times out (cold start), second succeeds
        mock_success = MagicMock()
        mock_success.__enter__.return_value.getcode.return_value = 200
        mock_success.__enter__.return_value.read.return_value = b'{"status": "ready"}'

        mock_urlopen.side_effect = [
            TimeoutError("Connection timed out"),
            mock_success,
        ]

        config = EndpointConfig(path="/ready", expected_status=200, status_field="status", allowed_status_values=("ready",))
        with patch("time.sleep"):
            result = probe_endpoint("Cold Service", "https://api.example.com/ready", config, max_retries=2, backoff_seconds=0.01)

        assert result.success is True
        assert result.attempts == 2
        assert result.is_cold_start is True

    @patch("urllib.request.urlopen")
    def test_persistent_failure(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("Server unreachable")

        config = EndpointConfig(path="/health")
        with patch("time.sleep"):
            result = probe_endpoint("Down Service", "https://api.example.com/health", config, max_retries=3, backoff_seconds=0.01)

        assert result.success is False
        assert result.attempts == 3
        assert "Failed after 3 attempts" in result.message

    @patch("urllib.request.urlopen")
    def test_invalid_json_shape(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.read.return_value = b'{"status": "degraded"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        config = EndpointConfig(path="/health", allowed_status_values=("ok", "ready"))
        with patch("time.sleep"):
            result = probe_endpoint("Degraded Service", "https://api.example.com/health", config, max_retries=1)

        assert result.success is False
        assert "JSON field 'status' was 'degraded'" in result.message


class TestServiceTargetConfig:
    def test_environment_overrides(self, monkeypatch):
        target = ServiceTarget(
            name="Custom API",
            env_var="CUSTOM_API_URL",
            default_url="https://default.example.com",
            enabled_env_var="MONITOR_CUSTOM",
        )
        assert target.base_url == "https://default.example.com"
        assert target.is_enabled is True

        monkeypatch.setenv("CUSTOM_API_URL", "https://custom.example.com/path/")
        monkeypatch.setenv("MONITOR_CUSTOM", "false")
        assert target.base_url == "https://custom.example.com/path"
        assert target.is_enabled is False


class TestRunMonitorOrchestration:
    @patch("monitor_backends.probe_endpoint")
    def test_run_monitor_all_pass(self, mock_probe):
        mock_probe.return_value = ProbeResult(
            target_name="Test",
            url="https://example.com/health",
            status_code=200,
            elapsed_seconds=0.1,
            attempts=1,
            success=True,
            is_cold_start=False,
            message="OK",
        )

        test_target = ServiceTarget(
            name="Mock Target",
            env_var="MOCK_URL",
            default_url="https://mock.example.com",
            enabled_env_var="MONITOR_MOCK",
            endpoints=[EndpointConfig(path="/health")],
        )

        exit_code = run_monitor([test_target], max_retries=1)
        assert exit_code == 0

    @patch("monitor_backends.probe_endpoint")
    def test_run_monitor_failure_propagation(self, mock_probe):
        mock_probe.return_value = ProbeResult(
            target_name="Failing Target",
            url="https://example.com/health",
            status_code=500,
            elapsed_seconds=0.1,
            attempts=3,
            success=False,
            is_cold_start=False,
            message="HTTP status 500",
        )

        test_target = ServiceTarget(
            name="Failing Target",
            env_var="FAIL_URL",
            default_url="https://fail.example.com",
            enabled_env_var="MONITOR_FAIL",
            endpoints=[EndpointConfig(path="/health")],
        )

        exit_code = run_monitor([test_target], max_retries=1)
        assert exit_code == 1

    @patch("monitor_backends.probe_endpoint")
    def test_run_monitor_disabled_target_skipped(self, mock_probe, monkeypatch):
        monkeypatch.setenv("MONITOR_SKIP", "0")
        test_target = ServiceTarget(
            name="Skipped Target",
            env_var="SKIP_URL",
            default_url="https://skip.example.com",
            enabled_env_var="MONITOR_SKIP",
            endpoints=[EndpointConfig(path="/health")],
        )

        exit_code = run_monitor([test_target])
        assert exit_code == 0
        mock_probe.assert_not_called()
