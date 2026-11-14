import csv
import json

import numpy as np
import pytest

from anpr.gate import ChangeGate
from anpr.output import console_table, format_timestamp, write_csv, write_json
from anpr.types import PlateResult

pytestmark = pytest.mark.unit


def thumb(value: int) -> np.ndarray:
    return np.full((90, 160), value, dtype=np.uint8)


def test_gate_first_frame_always_processed():
    assert ChangeGate(2.0).should_process(thumb(100))


def test_gate_skips_unchanged_and_accepts_change():
    gate = ChangeGate(2.0)
    assert gate.should_process(thumb(100))
    assert not gate.should_process(thumb(101))
    assert gate.should_process(thumb(140))
    assert (gate.processed, gate.skipped) == (2, 1)


def test_gate_compares_with_last_processed_not_last_seen():
    gate = ChangeGate(2.0)
    gate.should_process(thumb(100))
    assert not gate.should_process(thumb(101))
    assert not gate.should_process(thumb(101))
    assert gate.should_process(thumb(103))  # drift of 3 against the processed frame


def test_gate_forces_processing_after_max_skips():
    gate = ChangeGate(2.0, max_consecutive_skips=3)
    gate.should_process(thumb(100))
    results = [gate.should_process(thumb(100)) for _ in range(4)]
    assert results == [False, False, False, True]


def test_gate_disabled_with_zero_threshold():
    gate = ChangeGate(0.0)
    assert all(gate.should_process(thumb(100)) for _ in range(3))


def test_format_timestamp():
    assert format_timestamp(0) == "00:00.000"
    assert format_timestamp(3.2) == "00:03.200"
    assert format_timestamp(75.4567) == "01:15.457"
    assert format_timestamp(-1) == "00:00.000"


def test_writers_round_trip(tmp_path):
    results = [PlateResult("9KTA262", 3.2, 7.8, 0.91, 14)]
    write_json(tmp_path / "a.json", results, {"preset": "balanced"})
    write_csv(tmp_path / "a.csv", results)

    data = json.loads((tmp_path / "a.json").read_text())
    assert data["plates"][0] == {
        "plate": "9KTA262",
        "first_seen": "00:03.200",
        "last_seen": "00:07.800",
        "confidence": 0.91,
        "readings": 14,
    }
    assert data["run"]["preset"] == "balanced"
    with (tmp_path / "a.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["plate"] == "9KTA262"


def test_empty_results_are_valid_everywhere(tmp_path):
    write_json(tmp_path / "e.json", [], {})
    write_csv(tmp_path / "e.csv", [])
    assert json.loads((tmp_path / "e.json").read_text())["plates"] == []
    assert (tmp_path / "e.csv").read_text().startswith("plate,")
    assert console_table([]) == "No plates found."


def test_stage_timers_report_per_call_latency():
    from anpr.metrics import StageTimers

    timers = StageTimers()
    for ms in (10, 20, 30, 40, 100):
        timers.record("detect", ms / 1000)
    stats = timers.latency_ms({"decode": [0.024, 0.026]})
    assert stats["detect"]["calls"] == 5
    assert stats["detect"]["mean_ms"] == 40.0
    assert stats["detect"]["p50_ms"] == 30.0
    assert stats["detect"]["max_ms"] == 100.0
    assert stats["decode"]["mean_ms"] == 25.0
    assert timers.seconds["detect"] == pytest.approx(0.2)
