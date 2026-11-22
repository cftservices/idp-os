"""Tests for the milkdemo -> aios fouling sync (no network).

Fakes on the real contracts: TDengine rows in the column order of fouling_sql / cip_sql (ms
timestamps, cycle_start as BIGINT ms, the exchanger tag last), aios as plain JSON lists/objects."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import chain as C
import fouling_sync as FS

T0 = datetime(2026, 11, 20, 0, tzinfo=timezone.utc)
MODEL = {"process_constants": {"product_cp_kj_kg_k": 3.85, "boiler_efficiency": 0.9},
         "cost_model": {"gas_price_per_kwh": 0.06}, "food_safety": {"haccp_max_run_h": {"L1": 4.0, "L2": 4.0}}}


def history(cycles: int = 3) -> tuple[list[list], list[list]]:
    """Per cycle seven 0.25 h buckets five minutes apart, then a CIP 40 minutes after the start."""
    buckets, cips = [], []
    for c in range(cycles):
        start = T0 + timedelta(hours=c)
        for k in range(7):
            t = C.ms(start + timedelta(minutes=5 * (k + 1)))
            buckets.append([t, (k + 0.5) * 0.25, 0.01 * k, 91.5 - 0.3 * k, 11.0, 9900.0, 75.0, 17.6, C.ms(start),
                            "pasteurizer-01"])
        cips.append([C.ms(start + timedelta(minutes=40)), 1.75, 52.07, 37.5, "pasteurizer-01"])
    return buckets, cips


class FakeTd:
    def __init__(self, buckets: list[list], cips: list[list]) -> None:
        self.buckets, self.cips, self.sql = buckets, cips, []

    def query(self, sql: str) -> list[list]:
        C.check_select(sql)
        self.sql.append(sql)
        lo, hi = (int(x) for x in re.search(r"ts >= (\d+) and ts < (\d+)", sql).groups())
        rows = self.buckets if ".fouling " in sql else self.cips
        return [r for r in rows if lo <= r[0] < hi]


class FakeAios:
    NAMES = {"KPI-PAC-104": "Heat Recovery Ratio", "KPI-PAC-102": "Fouling Resistance",
             "KPI-PAC-105": "Run Length Between Cleanings", "KPI-PAC-106": "Cleaning Cost per Cycle"}

    def __init__(self) -> None:
        self.kpis: dict[str, dict] = {}
        self.points: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.next_id = 0

    def _out(self, k: dict) -> dict:
        pts = self.points.get(k["id"], [])
        return {**k, "current_at": max((p["at"] for p in pts), default=None)}

    def call(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.endswith("/kpis?archived=true"):
            return [self._out(k) for k in self.kpis.values()]
        if method == "POST" and path.endswith("/kpis"):
            self.next_id += 1
            kid = f"k{self.next_id}"
            self.kpis[kid] = {"id": kid, "name": self.NAMES[body["catalogue_kpi_id"]], "archived": False,
                              "value_inputs": {}, **body}
            return self._out(self.kpis[kid])
        if method == "PATCH":
            k = self.kpis[path.rsplit("/", 1)[1]]
            k.update(body)
            return self._out(k)
        if method == "POST" and path.endswith("/measurements"):
            self.points.setdefault(path.split("/")[-2], []).extend(body["items"])
            return {"added": len(body["items"])}
        if path.endswith("/detect"):
            return {"created": [], "already_known": 0, "no_finding": []}
        if path.endswith("/fouling/analyse"):
            return {"results": [{"scope": "Vla L1", "status": "computed", "action": "created", "amount": "123.45",
                                 "current_h": 1.75, "target_h": 2.5}]}
        raise AssertionError(f"unexpected {method} {path}")

    def by(self, cid: str) -> dict:
        return next(k for k in self.kpis.values() if k["catalogue_kpi_id"] == cid and k["scope"] == "Vla L1")


def sync(td, aios, now: datetime, **kw) -> FS.FoulingSync:
    return FS.FoulingSync(td, aios, "ws1", ["L1"], model=MODEL, scale=15.0, since=T0, now=lambda: now, **kw)


def test_buckets_carry_the_cycle_context_and_cips_split_cash_from_labour() -> None:
    b, c = history(1)
    pts = {**FS.bucket_points(b), **FS.cip_points(c)}
    p = pts["KPI-PAC-104"][0]
    assert p["context"]["cycle_start"] == T0.isoformat() and p["context"]["run_h"] == 0.125
    assert p["context"]["throughput_kg"] == 9900.0 and p["source"].startswith("demo")
    assert pts["KPI-PAC-106"][0]["value"] == 52.07 and pts["KPI-PAC-106"][0]["context"]["labour_eur"] == 37.5


def test_a_cip_without_prices_has_no_cost_never_zero() -> None:
    pts = FS.cip_points([[C.ms(T0), 1.75, None, None, "pasteurizer-01"]])
    assert pts["KPI-PAC-106"] == [] and len(pts["KPI-PAC-105"]) == 1


def test_run_hours_per_year_in_process_time() -> None:
    _, c = history(3)                      # 1.75 run hours per wall hour, 15 process hours per wall hour
    assert FS.run_hours_per_year(c, 15.0) == (1.75 * 2) / (2 * 15.0) * 8760
    assert FS.run_hours_per_year(c[:1], 15.0) is None


def test_first_run_creates_the_four_kpis_with_inputs_and_runs_the_analysis() -> None:
    b, c = history(3)
    aios = FakeAios()
    r = sync(FakeTd(b, c), aios, T0 + timedelta(hours=3)).run_once()
    assert len(aios.kpis) == 4
    rec = aios.by("KPI-PAC-104")
    vi = rec["value_inputs"]
    assert vi["haccp_max_run_h"]["value"] == 4.0 and vi["haccp_max_run_h"]["confidence"] == "assumed"
    assert vi["run_hours_per_year"]["confidence"] == "measured" and "cp_kj_kg_k" in vi
    assert aios.by("KPI-PAC-102")["critical"] == 0.10
    assert len(aios.points[rec["id"]]) == 21 and len(aios.points[aios.by("KPI-PAC-105")["id"]]) == 3
    assert r["schedule analysis"].startswith("Vla L1: computed")


def test_second_run_sends_only_what_is_new_to_the_millisecond() -> None:
    b, c = history(3)
    aios, td = FakeAios(), FakeTd(b, c)
    sync(td, aios, T0 + timedelta(hours=3)).run_once()
    again = sync(td, aios, T0 + timedelta(hours=3, minutes=10)).run_once()
    assert "schedule analysis" not in again
    assert all(v.startswith("nothing new") for k, v in again.items())
    b2, c2 = history(4)
    td.buckets, td.cips = b2, c2
    later = sync(td, aios, T0 + timedelta(hours=4)).run_once()
    assert later["KPI-PAC-104 · Vla L1"].startswith("7 added")
    ats = [p["at"] for p in aios.points[aios.by("KPI-PAC-104")["id"]]]
    assert len(ats) == len(set(ats)) == 28


def test_without_a_haccp_limit_in_the_model_the_input_stays_missing() -> None:
    b, c = history(3)
    aios = FakeAios()
    FS.FoulingSync(FakeTd(b, c), aios, "ws1", ["L1"], model={"process_constants": {}}, scale=15.0, since=T0,
                   now=lambda: T0 + timedelta(hours=3)).run_once()
    vi = aios.by("KPI-PAC-104")["value_inputs"]
    assert "haccp_max_run_h" not in vi and "boiler_efficiency" not in vi


def test_dry_run_writes_nothing() -> None:
    b, c = history(2)
    aios = FakeAios()
    r = sync(FakeTd(b, c), aios, T0 + timedelta(hours=2), dry_run=True).run_once()
    assert not aios.kpis and all(m == "GET" for m, _ in aios.calls)
    assert r["KPI-PAC-104 · Vla L1"].startswith("dry-run: 14")


def test_a_kpi_added_later_does_not_resend_the_buckets_of_its_neighbour() -> None:
    b, c = history(3)
    aios, td = FakeAios(), FakeTd(b, c)
    sync(td, aios, T0 + timedelta(hours=3)).run_once()
    del aios.kpis[aios.by("KPI-PAC-102")["id"]]            # PAC-102 comes back empty, PAC-104 has its history
    sync(td, aios, T0 + timedelta(hours=3, minutes=5)).run_once()
    ats = [p["at"] for p in aios.points[aios.by("KPI-PAC-104")["id"]]]
    assert len(ats) == len(set(ats)) == 21
    assert len(aios.points[aios.by("KPI-PAC-102")["id"]]) == 21
