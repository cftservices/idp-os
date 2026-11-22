"""Tests for the milkdemo -> aios value chain sync (no network).

The fakes copy the real contracts: TDengine REST rows as measured on 2026-11-22
([t_ms, kpi_id, input, n, sum] for the hourly query, [kpi_id, input, sum] for the totals), and the
aios chain API as plain JSON without an envelope (GET /kpis is a list with current_at)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import chain as C

T0 = datetime(2026, 11, 20, 0, tzinfo=timezone.utc)
COST = {"sales_price_per_pack": 1.35, "value_per_pack": 0.62}


def hour(h: int) -> datetime:
    return T0 + timedelta(hours=h)


def bucket_rows(hours: int, downtime=lambda h: 1.5, energy=lambda h: 13900.0) -> list[list]:
    """Twelve 5-minute buckets per hour, already summed per hour, like the probe on the VPS."""
    rows = []
    for h in range(hours):
        t = C.ms(hour(h))
        rows += [[t, "KPI-PRD-002", "downtime_hours", 12, downtime(h)], [t, "KPI-PRD-002", "planned_hours", 12, 15.0],
                 [t, "KPI-ENE-001", "energy_kwh", 12, energy(h)], [t, "KPI-ENE-001", "units_produced", 12, 100000.0]]
    return rows


TOTALS = [["KPI-PRD-001", "units_produced", 7305000.0], ["KPI-PRD-001", "planned_hours", 1042.5],
          ["KPI-PRD-001", "units_good", 7136340.0], ["KPI-PRD-001", "run_hours", 720.462292],
          ["KPI-ENE-001", "energy_kwh", 1000000.0], ["KPI-ENE-002", "energy_costs", 85000.0]]


class FakeTd:
    def __init__(self, rows: list[list], totals: list[list] | None = None) -> None:
        self.rows, self.totals, self.sql = rows, totals if totals is not None else TOTALS, []

    def query(self, sql: str) -> list[list]:
        self.sql.append(sql)
        if "interval(1h)" not in sql:
            return self.totals
        lo, hi = (int(x) for x in re.search(r"ts >= (\d+) and ts < (\d+)", sql).groups())
        line = re.search(r"`line` = '(\w+)'", sql).group(1)
        return [r for r in self.rows if lo <= r[0] < hi and line in ("L1", "L2")]


class FakeAios:
    NAMES = {"KPI-PRD-002": "Unplanned Downtime", "KPI-ENE-001": "Energy per Unit"}

    def __init__(self) -> None:
        self.kpis: dict[str, dict] = {}
        self.points: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.next_id = 0

    def _out(self, k: dict) -> dict:
        pts = self.points.get(k["id"], [])
        return {**k, "current_at": max((p["at"] for p in pts), default=None), "measurements": len(pts)}

    def call(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.endswith("/kpis?archived=true"):
            return [self._out(k) for k in self.kpis.values()]
        if method == "POST" and path.endswith("/kpis"):
            self.next_id += 1
            kid = f"k{self.next_id}"
            self.kpis[kid] = {"id": kid, "name": self.NAMES[body["catalogue_kpi_id"]], "archived": False, **body}
            return self._out(self.kpis[kid])
        if method == "PATCH" and "/kpis/" in path:
            k = self.kpis[path.rsplit("/", 1)[1]]
            k.update(body)
            return self._out(k)
        if method == "POST" and path.endswith("/measurements"):
            kid = path.split("/")[-2]
            self.points.setdefault(kid, []).extend(body["items"])
            return {"added": len(body["items"]), "kpi": self._out(self.kpis[kid])}
        if method == "POST" and path.endswith("/detect"):
            return {"created": [{"id": "o1"}], "already_known": 0, "no_finding": []}
        raise AssertionError(f"unexpected {method} {path}")

    def by(self, cid: str, line: str) -> dict:
        return next(k for k in self.kpis.values() if k["catalogue_kpi_id"] == cid and k["scope"] == C.scope(line))


def sync(td, aios, now: datetime, **kw) -> C.ChainSync:
    return C.ChainSync(td, aios, "ws1", ["L1", "L2"], cost=COST, since=T0, now=lambda: now, **kw)


# --------------------------------------------------------------------------- pure


def test_hourly_value_is_the_ratio_of_the_sums_never_a_rolling_value() -> None:
    v = C.hourly_values(bucket_rows(2, downtime=lambda h: 3.0 if h else 1.5), "KPI-PRD-002")
    assert v == [(hour(0), 10.0), (hour(1), 20.0)]
    assert C.hourly_values(bucket_rows(1), "KPI-ENE-001") == [(hour(0), 0.139)]


def test_an_hour_without_numerator_thin_coverage_or_zero_denominator_gives_no_value() -> None:
    t = C.ms(hour(0))
    assert C.hourly_values([[t, "KPI-PRD-002", "planned_hours", 12, 15.0]], "KPI-PRD-002") == []
    assert C.hourly_values([[t, "KPI-PRD-002", "downtime_hours", 12, 1.0],
                            [t, "KPI-PRD-002", "planned_hours", 12, 0.0]], "KPI-PRD-002") == []
    assert C.hourly_values([[t, "KPI-PRD-002", "downtime_hours", 5, 1.0],
                            [t, "KPI-PRD-002", "planned_hours", 5, 6.0]], "KPI-PRD-002") == []


def test_an_hour_is_final_only_fifteen_minutes_after_it_closed() -> None:
    assert C.closed_until(hour(10) + timedelta(minutes=10)) == hour(9)    # 09:00-10:00 not yet final
    assert C.closed_until(hour(10) + timedelta(minutes=20)) == hour(10)   # now it is


def test_next_hour_follows_the_latest_measurement_in_aios() -> None:
    assert C.next_hour(None, T0 + timedelta(minutes=10)) == T0
    assert C.next_hour(hour(5).isoformat(), T0) == hour(6)
    assert C.next_hour("2026-11-20T05:00:00Z", T0) == hour(6)


def test_value_inputs_come_from_the_historian_and_the_cost_model_with_labels() -> None:
    tot = C.totals(TOTALS)
    e = C.value_inputs("KPI-ENE-001", tot, COST)
    assert round(e["units_per_year"]["value"]) == round(7305000 / 1042.5 * 8760)
    assert e["units_per_year"]["confidence"] == "measured"
    assert e["energy_price"]["value"] == 0.085 and e["energy_price"]["confidence"] == "assumed"
    d = C.value_inputs("KPI-PRD-002", tot, COST)
    assert round(d["good_rate"]["value"]) == 9905 and d["planned_hours_per_year"]["value"] == 8760
    assert d["contribution_margin"]["value"] == 0.73 and d["contribution_margin"]["confidence"] == "assumed"
    assert "capacity_constrained" not in d   # the catalogue default (0) stays until Operations confirms


def test_a_missing_input_stays_missing_never_zero() -> None:
    d = C.value_inputs("KPI-PRD-002", {}, {})
    assert set(d) == {"planned_hours_per_year"}
    assert C.value_inputs("KPI-ENE-001", {("KPI-ENE-001", "energy_kwh"): 0.0}, COST) == {}


def test_inputs_changed_ignores_noise_below_one_percent() -> None:
    a = {"good_rate": {"value": 9905.0}}
    assert not C.inputs_changed(a, {"good_rate": {"value": 9950.0}})
    assert C.inputs_changed(a, {"good_rate": {"value": 10100.0}})
    assert C.inputs_changed(a, {"good_rate": {"value": 9905.0}, "contribution_margin": {"value": 0.73}})


# --------------------------------------------------------------------------- sync


def test_first_run_creates_four_kpis_sends_closed_hours_and_runs_detection() -> None:
    td, aios = FakeTd(bucket_rows(30)), FakeAios()
    report = sync(td, aios, hour(24) + timedelta(minutes=20)).run_once()
    assert len(aios.kpis) == 4
    k = aios.by("KPI-PRD-002", "L1")
    assert k["name"] == "Unplanned Downtime · Vla L1" and k["frequency"] == "hourly"
    assert k["value_inputs"]["good_rate"]["confidence"] == "measured"
    pts = aios.points[k["id"]]
    assert len(pts) == 24 and pts[-1]["at"] == hour(23).isoformat()       # hour 24 is not final yet
    assert all(p["source"].startswith("demo") for p in pts)                # aios labels it fictional
    assert report["detection"].startswith("1 new")


def test_second_run_sends_only_new_hours_and_asks_the_historian_only_for_those() -> None:
    td, aios = FakeTd(bucket_rows(30)), FakeAios()
    sync(td, aios, hour(24) + timedelta(minutes=20)).run_once()
    s = sync(td, aios, hour(24) + timedelta(minutes=40))
    again = s.run_once()
    assert "detection" not in again and all(len(p) == 24 for p in aios.points.values())
    later = sync(td, aios, hour(26) + timedelta(minutes=20)).run_once()
    assert all(len(p) == 26 for p in aios.points.values())
    assert later["KPI-ENE-001 · Vla L2"].startswith("2 hours added")
    assert f"ts >= {C.ms(hour(24))}" in td.sql[-1]                         # no re-read of the history


def test_an_archived_kpi_is_left_alone_not_recreated() -> None:
    td, aios = FakeTd(bucket_rows(30)), FakeAios()
    sync(td, aios, hour(10) + timedelta(minutes=20)).run_once()
    aios.by("KPI-ENE-001", "L1")["archived"] = True
    r = sync(td, aios, hour(20) + timedelta(minutes=20)).run_once()
    assert len(aios.kpis) == 4 and r["KPI-ENE-001 · Vla L1"] == "archived in aios, skipped"
    assert len(aios.points[aios.by("KPI-ENE-001", "L1")["id"]]) == 10


def test_dry_run_reads_and_writes_nothing() -> None:
    td, aios = FakeTd(bucket_rows(5)), FakeAios()
    r = sync(td, aios, hour(5) + timedelta(minutes=20), dry_run=True).run_once()
    assert not aios.kpis and all(m == "GET" for m, _ in aios.calls)
    assert r["KPI-PRD-002 · Vla L1"].startswith("dry-run: 5 hours")


def test_value_inputs_are_refreshed_once_a_day_and_only_when_they_moved() -> None:
    td, aios = FakeTd(bucket_rows(30)), FakeAios()
    s = sync(td, aios, hour(10) + timedelta(minutes=20))
    s.run_once()
    td.totals = [r if r[1] != "units_good" else [r[0], r[1], r[2] * 1.2] for r in TOTALS]
    s.run_once()                                                          # same day: no refresh
    assert round(aios.by("KPI-PRD-002", "L1")["value_inputs"]["good_rate"]["value"]) == 9905
    s.now = lambda: hour(34)
    r = s.run_once()                                                      # next day: refreshed
    assert round(aios.by("KPI-PRD-002", "L1")["value_inputs"]["good_rate"]["value"]) == round(9905 * 1.2)
    assert "value inputs updated" in r["KPI-PRD-002 · Vla L1"]


def test_a_kpi_added_later_does_not_resend_the_history_of_its_neighbour() -> None:
    # The historian is asked from the oldest "next hour" of the line, so the new KPI gets its whole
    # history; the KPI that already had it must get only its new hours, never a duplicate.
    td, aios = FakeTd(bucket_rows(30)), FakeAios()
    sync(td, aios, hour(10) + timedelta(minutes=20)).run_once()
    gone = aios.by("KPI-ENE-001", "L1")
    del aios.kpis[gone["id"]]
    sync(td, aios, hour(20) + timedelta(minutes=20)).run_once()
    prd = [p["at"] for p in aios.points[aios.by("KPI-PRD-002", "L1")["id"]]]
    assert len(prd) == len(set(prd)) == 20
    assert len(aios.points[aios.by("KPI-ENE-001", "L1")["id"]]) == 20


def test_the_client_sends_only_a_single_select_because_tdengine_does_not_enforce_the_grant() -> None:
    import pytest
    C.check_select(C.hourly_sql("idp", "L1", hour(0), hour(1)))
    C.check_select(C.totals_sql("idp", "L1", hour(0), hour(1)))
    for bad in ("insert into idp.x values (now, 1)", "drop table idp.kpi_input",
                "select 1; drop database idp", "  create database x"):
        with pytest.raises(ValueError):
            C.check_select(bad)
    with pytest.raises(ValueError):
        C.Td("http://td", "u", "p").query("drop database idp")   # refused before any network call
