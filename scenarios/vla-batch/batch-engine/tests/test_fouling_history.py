"""Fouling Intelligence: buckets per cleaning cycle and CIP costs into TDengine.

Against hand-computed values on samples whose temperatures give an exact U (the helper of
test_meters), and through the real Mongo fake and bvip.take_snapshot."""
from __future__ import annotations

from datetime import timedelta

import pytest

from vla import bvip, fouling_history as F, kpi_history, meters
from tests.test_meters import AREA, CFG, CP, T0, _db, iso, sample_for_u


def put(db, minute: float, u: float, run_h: float, flow: float = 11.0, line: str = "L1") -> None:
    s = sample_for_u(u, run_h, flow)
    db.dw_pasteur_samples.insert_one({"line": line, "ts": iso(T0 + timedelta(minutes=minute)), **s,
                                      "u": meters.u_value(s, CP, AREA)})


def cip(db, minute: float, run_h: float | None = 1.5, baseline: bool = False) -> None:
    row = {"line": "L1", "ts": iso(T0 + timedelta(minutes=minute)), "cip_count": 1, "baseline": baseline}
    if not baseline:
        row.update(water_L=9000, caustic_kg=15, acid_kg=8, heat_kwh=200, electricity_kwh=7.5,
                   duration_s=3600, past_run_h=run_h)
    db.dw_cip_cycles.insert_one(row)


def snap(db, minute: float = 60):
    return bvip.take_snapshot(db, now=T0 + timedelta(minutes=minute))


def test_complete_buckets_per_cycle_with_rf_against_the_clean_u_of_that_cycle() -> None:
    db = _db()
    for m, u, h in [(1, 3.8, 0.05), (2, 3.8, 0.10), (3, 3.6, 0.30), (4, 3.4, 0.40),   # cycle 1
                    (6, 3.8, 0.05), (7, 3.7, 0.30)]:                                 # cycle 2 (running)
        put(db, m, u, h)
    b = F.buckets(snap(db).pasteur["L1"], CFG)
    # cycle 1: bucket 0 (run_h 0-0.25) and bucket 1 (0.25-0.5, complete because cycle 1 ended);
    # cycle 2: bucket 0 is complete (a later sample sits in bucket 1), bucket 1 is still filling
    assert [(x["run_h"], x["n"]) for x in b] == [(0.125, 2), (0.375, 2), (0.125, 1)]
    assert b[1]["rf"] == pytest.approx(((1 / 3.6 - 1 / 3.8) + (1 / 3.4 - 1 / 3.8)) / 2, rel=1e-3)
    assert b[0]["rf"] == pytest.approx(0, abs=1e-6)
    assert b[2]["cycle_start"] == T0 + timedelta(minutes=6)
    assert b[1]["recovery_pct"] == pytest.approx((69.4 - 5) / (75 - 5) * 100)
    assert b[1]["throughput_kg"] == pytest.approx(11.0 * 0.25 * 3600)       # process seconds


def test_a_cycle_not_seen_from_its_start_is_left_out_not_written_half() -> None:
    db = _db()
    for m, u, h in [(1, 3.4, 0.9), (2, 3.3, 1.2),       # started before Mongo's window: no U_clean
                    (4, 3.8, 0.05), (5, 3.6, 0.30), (6, 3.5, 0.55)]:
        put(db, m, u, h)
    b = F.buckets(snap(db).pasteur["L1"], CFG)
    assert all(x["cycle_start"] == T0 + timedelta(minutes=4) for x in b) and len(b) == 2


def test_no_flow_is_no_sample_and_flow_weights_the_recovery() -> None:
    db = _db()
    put(db, 1, 3.8, 0.05)
    put(db, 2, 3.8, 0.10, flow=0.5)                      # milk stopped: not a sample
    put(db, 3, 3.6, 0.30)
    b = F.buckets(snap(db).pasteur["L1"], CFG)
    assert b[0]["n"] == 1


def test_cip_rows_split_cash_and_labour_and_skip_the_baseline() -> None:
    db = _db()
    cip(db, 0, baseline=True)
    cip(db, 5, run_h=1.5)
    rows = F.cip_rows(snap(db).cip_cycles["L1"], CFG)
    assert len(rows) == 1
    assert rows[0]["cash_eur"] == pytest.approx(15 * 0.55 + 8 * 0.9 + 9 * 2.5 + 200 * 0.06 + 7.5 * 0.18)
    assert rows[0]["labour_eur"] == pytest.approx(0.5 * 80)
    # the same total as KPI-PAC-106, by construction
    k = bvip.kpi_values(snap(db), "L1", T0, T0 + timedelta(minutes=10), CFG, bvip.load_catalogue())
    assert k["KPI-PAC-106"]["value"] == pytest.approx(rows[0]["cash_eur"] + rows[0]["labour_eur"])


def test_without_prices_the_cost_is_null_never_zero() -> None:
    db = _db()
    cip(db, 5)
    cfg = bvip.Config(time_scale=15.0, product_cp=CP, pasteuriser_ref_flow=11.0, pasteuriser_area=AREA)
    row = F.cip_rows(snap(db).cip_cycles["L1"], cfg)[0]
    assert row["cash_eur"] is None and row["labour_eur"] is None and row["run_h"] == 1.5


def test_parts_render_both_stables_and_respect_since() -> None:
    db = _db()
    for m, u, h in [(1, 3.8, 0.05), (2, 3.6, 0.30), (3, 3.5, 0.55)]:
        put(db, m, u, h)
    cip(db, 30)
    ps = F.parts(snap(db), CFG, "idp", ("L1", "L2"))
    assert sum("USING idp.fouling TAGS ('L1', 'pasteurizer-01')" in p for p in ps) == 2
    assert sum("USING idp.cip_cycle" in p for p in ps) == 1
    later = F.parts(snap(db), CFG, "idp", ("L1",), since=T0 + timedelta(minutes=10))
    assert len(later) == 1 and "cip_cycle" in later[0]


class RecordingTd:
    db = "idp"

    def __init__(self) -> None:
        self.stmts: list[str] = []

    def sql(self, stmt):
        self.stmts.append(stmt)
        return {"code": 0, "data": []}


def test_the_kpi_writer_creates_the_stables_and_writes_fouling_on_the_same_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("FOULING_WRITER", "1")
    db = _db()
    for m, u, h in [(1, 3.8, 0.05), (2, 3.6, 0.30), (3, 3.5, 0.55)]:
        put(db, m, u, h)
    td = RecordingTd()
    w = kpi_history.KpiWriter(db, td=td, cfg_factory=lambda: CFG, now=lambda: T0 + timedelta(minutes=10))
    w.ensure_schema()
    assert any("STABLE IF NOT EXISTS idp.fouling" in s for s in td.stmts)
    w.write([T0], [T0 + timedelta(minutes=5)], fouling_since=None)
    assert any("USING idp.fouling" in s for s in td.stmts) and w.status["fouling"]["rows"] == 2


def test_fouling_writer_can_be_switched_off(monkeypatch) -> None:
    monkeypatch.setenv("FOULING_WRITER", "0")
    td = RecordingTd()
    w = kpi_history.KpiWriter(_db(), td=td, cfg_factory=lambda: CFG, now=lambda: T0)
    w.ensure_schema()
    assert not any("fouling" in s for s in td.stmts)
