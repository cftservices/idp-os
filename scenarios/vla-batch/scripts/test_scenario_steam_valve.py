"""Tests for scenario_steam_valve.py without a factory or engine.

asyncua is stubbed, factory_call and completed_since are replaced by fakes. Run:
    python -m pytest scenarios/vla-batch/scripts/test_scenario_steam_valve.py -q
"""
from __future__ import annotations

import importlib
import json
import sys
import types

import pytest


@pytest.fixture()
def sv(monkeypatch, tmp_path):
    stub = types.ModuleType("asyncua")
    stub.Client = object
    stub.ua = types.SimpleNamespace(Variant=lambda *a: a, VariantType=types.SimpleNamespace(String=0, Double=1))
    monkeypatch.setitem(sys.modules, "asyncua", stub)
    monkeypatch.setenv("SCENARIO_POLL_S", "0")
    monkeypatch.setenv("SCENARIO_LOG", str(tmp_path / "single.jsonl"))
    monkeypatch.setenv("SCENARIO_LOOP_LOG", str(tmp_path / "loop.jsonl"))
    sys.modules.pop("scenario_steam_valve", None)
    monkeypatch.syspath_prepend(str(__import__("pathlib").Path(__file__).parent))
    mod = importlib.import_module("scenario_steam_valve")
    mod._log_path = str(tmp_path / "single.jsonl")
    mod.BACKOFF_MIN_S = 0.0
    mod.stop.clear()
    mod.line_paused = lambda line: False   # no engine here; tests that pause override it
    return mod


class FakeEngine:
    """Completes one batch per poll, on whatever line is asked."""

    def __init__(self, verdict_lag: int = 0):
        self.n = {}
        self.verdict_lag = verdict_lag

    def __call__(self, line, start):
        self.n[line] = self.n.get(line, 0) + 1
        return [{"batch_id": f"{line}-{i}", "completed_at": f"{start}-{i:04d}",
                 "verdict": "APPROVED", "end_viscosity_cP": 260.0}
                for i in range(self.n[line])]


def test_cycle_sets_faults_in_phase_order(sv, monkeypatch):
    calls = []
    monkeypatch.setattr(sv, "factory_call", lambda url, action, **ctx: calls.append(action) or 0)
    monkeypatch.setattr(sv, "completed_since", FakeEngine())
    n = sv.run_cycle("L1", "opc.tcp://x", "2026-11-19T00:00:00", cycle=1)
    assert n == 52
    assert calls == ["clear", 0.06, 0.5, "clear"]


def test_transient_engine_error_is_retried_and_fault_reasserted(sv, monkeypatch):
    calls, fails = [], {"left": 2}
    engine = FakeEngine()

    def flaky(line, start):
        if fails["left"]:
            fails["left"] -= 1
            raise OSError("connection refused")
        return engine(line, start)

    monkeypatch.setattr(sv, "factory_call", lambda url, action, **ctx: calls.append(action) or 0)
    monkeypatch.setattr(sv, "completed_since", flaky)
    sv.run_cycle("L2", "opc.tcp://x", "2026-11-19T00:00:00", cycle=2)
    # start clear, reassert of the intended (still "clear") after recovery, then the phases
    assert calls == ["clear", "clear", 0.06, 0.5, "clear"]
    events = [json.loads(r)["event"] for r in open(sv._log_path, encoding="utf-8")]
    assert events.count("engine_unreachable") == 1 and events.count("engine_recovered") == 1


def test_stop_clears_fault_on_both_lines(sv, monkeypatch):
    cleared = []

    def fake_call(url, action, **ctx):
        if action == "clear" and ctx.get("reason") == "cleanup":
            cleared.append(url)
        if action == 0.06:
            sv.stop.set()  # SIGTERM arrives during the drift phase
        return 0

    monkeypatch.setattr(sv, "factory_call", fake_call)
    monkeypatch.setattr(sv, "completed_since", FakeEngine())
    sv.main_loop()
    # cleanup at start (both lines) and at stop (both lines)
    assert cleared.count(sv.OPCUA_BY_LINE["L1"]) == 2
    assert cleared.count(sv.OPCUA_BY_LINE["L2"]) == 2
    events = [json.loads(r)["event"] for r in open(sv.LOOP_LOG, encoding="utf-8")]
    assert "loop_stopped" in events


def test_loop_alternates_lines_and_resumes_cycle_number(sv, monkeypatch):
    with open(sv.LOOP_LOG, "w", encoding="utf-8") as f:
        f.write(json.dumps({"event": "cycle_end", "cycle": 7, "line": "L1"}) + "\n")
    lines = []

    def fake_cycle(line, url, start, cycle=None):
        lines.append((cycle, line))
        if len(lines) == 3:
            sv.stop.set()
        return 52

    monkeypatch.setattr(sv, "factory_call", lambda *a, **k: 0)
    monkeypatch.setattr(sv, "run_cycle", fake_cycle)
    sv.main_loop()
    assert lines == [(8, "L2"), (9, "L1"), (10, "L2")]


def test_batches_without_verdict_are_not_counted(sv, monkeypatch):
    rows = [{"batch_id": "a", "state": "COMPLETE", "verdict": "APPROVED", "completed_at": "2026-11-19T01"},
            {"batch_id": "b", "state": "COMPLETE", "verdict": None, "completed_at": "2026-11-19T02"},
            {"batch_id": "c", "state": "FILLING", "verdict": None, "completed_at": None},
            {"batch_id": "d", "state": "COMPLETE", "verdict": "REJECTED", "completed_at": "2026-11-18T23"}]

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(rows).encode()

    monkeypatch.setattr(sv.urllib.request, "urlopen", lambda url, timeout: Resp())
    got = sv.completed_since("L1", "2026-11-19T00")
    assert [b["batch_id"] for b in got] == ["a"]


def test_clear_rc_1_is_success_but_inject_rc_5_is_not(sv, monkeypatch):
    async def fake(url, action):
        return 1 if action == "clear" else 5

    monkeypatch.setattr(sv, "_call", fake)
    assert sv.factory_call("u", "clear") == 1
    with pytest.raises(RuntimeError):
        sv.factory_call("u", 0.5)


def test_demo_pause_clears_fault_holds_cycle_and_reasserts_after(sv, monkeypatch):
    """While a presenter has the line the fault is cleared and nothing is counted; after
    the pause the fault of the current phase is set again and the cycle finishes."""
    calls = []
    monkeypatch.setattr(sv, "factory_call", lambda url, action, **ctx: calls.append(action) or 0)
    engine = FakeEngine()
    polls = {"n": 0}

    def paused(line):
        polls["n"] += 1
        return 30 <= polls["n"] < 33   # three polls paused, in the drift phase (25..35)

    monkeypatch.setattr(sv, "line_paused", paused)
    monkeypatch.setattr(sv, "completed_since", engine)
    n = sv.run_cycle("L1", "opc.tcp://x", "2026-11-19T00:00:00", cycle=1)
    assert n == 52
    # start clear, drift 0.06, demo-pause clear, reassert 0.06, out_of_spec 0.5, repaired clear
    assert calls == ["clear", 0.06, "clear", 0.06, 0.5, "clear"]
    log = [json.loads(r) for r in open(sv._log_path, encoding="utf-8")]
    events = [r["event"] for r in log]
    assert events.count("demo_paused") == 1 and events.count("demo_resumed") == 1
    # the engine was not asked for batches while paused
    assert engine.n["L1"] == polls["n"] - 3


def test_only_autopilot_batches_count(sv, monkeypatch):
    rows = [
        {"batch_id": "a", "state": "COMPLETE", "verdict": "APPROVED",
         "completed_at": "2026-11-20T10:00", "created_by": "autopilot"},
        {"batch_id": "b", "state": "COMPLETE", "verdict": "APPROVED",
         "completed_at": "2026-11-20T10:01", "created_by": "op-01"},
        {"batch_id": "c", "state": "COMPLETE", "verdict": "APPROVED",
         "completed_at": "2026-11-20T10:02", "created_by": None},
        {"batch_id": "d", "state": "COMPLETE", "verdict": "APPROVED",
         "completed_at": "2026-11-20T10:03"},   # older engine without the field
    ]

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(rows).encode()

    monkeypatch.setattr(sv.urllib.request, "urlopen", lambda *a, **k: Resp())
    got = [b["batch_id"] for b in sv.completed_since("L1", "2026-11-20T00:00")]
    assert got == ["a", "d"]
