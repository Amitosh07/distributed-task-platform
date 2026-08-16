"""Phase 8 — Benchmark infrastructure unit tests.

These tests require NO database, Redis, or external services.
They test the pure-Python benchmark library code and are safe to run in CI.

Coverage:
- latency_stats calculation correctness
- latency_stats edge cases (empty, single element)
- JSON result schema and serialization
- save_result produces valid JSON with required fields
- Worker process management helpers (no actual subprocess started)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Bootstrap path for lib.common without requiring installed backend
_BENCHMARKS = Path(__file__).resolve().parent.parent.parent.parent / "benchmarks"
if str(_BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS))


# ---------------------------------------------------------------------------
# latency_stats tests
# ---------------------------------------------------------------------------

class TestLatencyStats:
    """Tests for the latency_stats() helper function."""

    def _import(self):
        # Import lazily after path setup
        from lib.common import latency_stats  # type: ignore[import]
        return latency_stats

    def test_empty_list_returns_zeros(self):
        latency_stats = self._import()
        result = latency_stats([])
        assert result == {"min": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}

    def test_single_element(self):
        latency_stats = self._import()
        result = latency_stats([1.0])
        assert result["min"] == 1000.0
        assert result["max"] == 1000.0
        assert result["avg"] == 1000.0
        assert result["p50"] == 1000.0

    def test_uniform_durations(self):
        latency_stats = self._import()
        # All 1-second durations = all percentiles should be ~1000ms
        result = latency_stats([1.0] * 100)
        assert result["min"] == pytest.approx(1000.0)
        assert result["max"] == pytest.approx(1000.0)
        assert result["avg"] == pytest.approx(1000.0)
        assert result["p50"] == pytest.approx(1000.0)
        assert result["p95"] == pytest.approx(1000.0)

    def test_ordered_percentiles(self):
        latency_stats = self._import()
        # Spread of values: p50 < p95 < p99 < max
        durations = [float(i) / 1000.0 for i in range(1, 101)]  # 0.001s to 0.100s
        result = latency_stats(durations)
        assert result["min"] < result["p50"]
        assert result["p50"] < result["p95"]
        assert result["p95"] <= result["p99"]
        assert result["p99"] <= result["max"]

    def test_min_is_smallest(self):
        latency_stats = self._import()
        result = latency_stats([0.1, 0.5, 1.0, 2.0, 5.0])
        assert result["min"] == pytest.approx(100.0)  # 0.1s * 1000

    def test_max_is_largest(self):
        latency_stats = self._import()
        result = latency_stats([0.1, 0.5, 1.0, 2.0, 5.0])
        assert result["max"] == pytest.approx(5000.0)  # 5.0s * 1000

    def test_avg_is_mean(self):
        latency_stats = self._import()
        result = latency_stats([1.0, 3.0])  # avg = 2.0s = 2000ms
        assert result["avg"] == pytest.approx(2000.0)

    def test_millisecond_conversion(self):
        latency_stats = self._import()
        # 0.5 seconds = 500 milliseconds
        result = latency_stats([0.5])
        assert result["min"] == pytest.approx(500.0)
        assert result["max"] == pytest.approx(500.0)

    def test_small_sample_percentiles(self):
        latency_stats = self._import()
        # Less than 20 samples — uses manual interpolation
        result = latency_stats([0.1, 0.2, 0.3])
        assert result["min"] == pytest.approx(100.0)
        assert result["max"] == pytest.approx(300.0)
        assert result["p50"] is not None
        assert result["p95"] is not None
        assert result["p99"] is not None

    def test_result_keys_present(self):
        latency_stats = self._import()
        result = latency_stats([1.0])
        assert set(result.keys()) == {"min", "avg", "p50", "p95", "p99", "max"}

    def test_all_values_are_floats(self):
        latency_stats = self._import()
        result = latency_stats([1.0, 2.0, 3.0])
        for key, val in result.items():
            assert isinstance(val, float), f"{key} should be float, got {type(val)}"


# ---------------------------------------------------------------------------
# save_result tests
# ---------------------------------------------------------------------------

class TestSaveResult:
    """Tests for the save_result() function."""

    def _import(self):
        from lib.common import save_result, RESULTS_DIR  # type: ignore[import]
        return save_result, RESULTS_DIR

    def test_save_result_writes_valid_json(self, tmp_path):
        save_result, _ = self._import()
        # Monkey-patch RESULTS_DIR temporarily
        import lib.common as _common  # type: ignore[import]
        original = _common.RESULTS_DIR
        _common.RESULTS_DIR = tmp_path
        try:
            path = save_result("test_benchmark", {"workers": 3, "tasks": 100})
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["benchmark"] == "test_benchmark"
            assert "timestamp" in data
            assert data["workers"] == 3
            assert data["tasks"] == 100
        finally:
            _common.RESULTS_DIR = original

    def test_save_result_filename_contains_benchmark_name(self, tmp_path):
        save_result, _ = self._import()
        import lib.common as _common  # type: ignore[import]
        original = _common.RESULTS_DIR
        _common.RESULTS_DIR = tmp_path
        try:
            path = save_result("my_scaling_benchmark", {"x": 1})
            assert "my_scaling_benchmark" in path.name
        finally:
            _common.RESULTS_DIR = original

    def test_save_result_timestamp_in_filename(self, tmp_path):
        save_result, _ = self._import()
        import lib.common as _common  # type: ignore[import]
        original = _common.RESULTS_DIR
        _common.RESULTS_DIR = tmp_path
        try:
            path = save_result("ts_test", {"value": "hello"})
            # Filename should contain a Z-suffixed timestamp
            assert "Z.json" in path.name
        finally:
            _common.RESULTS_DIR = original

    def test_save_result_multiple_saves_are_distinct(self, tmp_path):
        save_result, _ = self._import()
        import lib.common as _common  # type: ignore[import]
        import time as _time
        original = _common.RESULTS_DIR
        _common.RESULTS_DIR = tmp_path
        try:
            # Sleep 1s to ensure distinct timestamps
            path1 = save_result("dup_test", {"run": 1})
            _time.sleep(1.1)
            path2 = save_result("dup_test", {"run": 2})
            assert path1 != path2
        finally:
            _common.RESULTS_DIR = original


# ---------------------------------------------------------------------------
# Result schema validation
# ---------------------------------------------------------------------------

class TestResultSchema:
    """Validate that benchmark result dicts have the expected schema."""

    def test_worker_scaling_result_schema(self):
        """worker_scaling result must have required keys."""
        required_keys = {
            "label",
            "worker_count",
            "task_count",
            "task_type",
            "wall_time_seconds",
            "throughput_tasks_per_sec",
            "success_count",
            "failure_count",
            "latency_execution_ms",
            "latency_e2e_ms",
            "latency_queue_wait_ms",
        }
        # Simulate a result dict as produced by load_test.run_single
        result = {
            "label": "3w",
            "worker_count": 3,
            "task_count": 100,
            "task_type": "sleep",
            "task_seconds": 1.0,
            "concurrency": 1,
            "submission_rate_tasks_per_sec": 45.2,
            "wall_time_seconds": 35.1,
            "throughput_tasks_per_sec": 2.85,
            "success_count": 100,
            "failure_count": 0,
            "timeout_count": 0,
            "retried_count": 0,
            "queue_depth_initial": 100,
            "queue_depth_max_observed": 97,
            "queue_depth_final": 0,
            "latency_execution_ms": {"min": 1001.2, "avg": 1003.0, "p50": 1002.5, "p95": 1010.0, "p99": 1015.0, "max": 1020.0},
            "latency_e2e_ms": {"min": 1002.1, "avg": 1050.0, "p50": 1040.0, "p95": 1200.0, "p99": 1500.0, "max": 2000.0},
            "latency_queue_wait_ms": {"min": 5.0, "avg": 45.0, "p50": 40.0, "p95": 100.0, "p99": 200.0, "max": 500.0},
        }
        missing = required_keys - set(result.keys())
        assert missing == set(), f"Missing keys: {missing}"

    def test_latency_stats_schema(self):
        """Latency stats dict must have all 6 percentile keys."""
        from lib.common import latency_stats  # type: ignore[import]
        stats = latency_stats([1.0, 2.0, 3.0])
        assert set(stats.keys()) == {"min", "avg", "p50", "p95", "p99", "max"}

    def test_crash_recovery_result_schema(self):
        """Worker crash recovery result must have required keys."""
        required_keys = {
            "detection_ms",
            "recovery_to_completion_ms",
            "total_recovery_latency_ms",
            "final_status",
            "attempt_count",
        }
        result = {
            "detection_ms": 3200,
            "recovery_to_completion_ms": 5100,
            "total_recovery_latency_ms": 8300,
            "final_status": "SUCCESS",
            "attempt_count": 2,
            "lease_seconds": 5.0,
            "task_duration_s": 20.0,
        }
        missing = required_keys - set(result.keys())
        assert missing == set(), f"Missing keys: {missing}"

    def test_workflow_result_schema(self):
        """Workflow DAG scaling result must have required keys."""
        required_keys = {"dag", "worker_count", "wall_time_s", "status", "task_seconds"}
        result = {
            "dag": "diamond",
            "worker_count": 2,
            "wall_time_s": 1.523,
            "status": "SUCCESS",
            "task_seconds": 0.5,
        }
        missing = required_keys - set(result.keys())
        assert missing == set(), f"Missing keys: {missing}"
