"""Tests for the milkdemo -> aios CI sync (no network)."""
from __future__ import annotations

import sync as S


def _b(i: str, day: str, packs: float, rejects: float, verdict: str = "APPROVED", visc: float = 260.0) -> dict:
    return {"batch_id": i, "completed_at": f"{day}T10:00:0{i[-1]}+00:00", "recipe_id": "chocolate-vla-1L",
            "peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0, "cook_heatup_sec": 150.0,
            "end_viscosity_cP": visc, "spec_min_cP": 150, "spec_max_cP": 300, "packs_total": packs,
            "reject_count": rejects, "verdict": verdict, "fault": None, "unrelated": "x"}


def test_batch_row_keeps_types_and_drops_unknown_fields() -> None:
    r = S.batch_row(_b("B1", "2026-11-17", 5000, 20))
    assert r["end_viscosity_cP"] == 260.0 and r["spec_min_cP"] == 150.0
    assert r["fault"] is None and "unrelated" not in r


def test_merge_appends_new_batches_once_in_completion_order() -> None:
    old = [S.batch_row(_b("B2", "2026-11-17", 5000, 10))]
    new = [S.batch_row(_b("B1", "2026-11-16", 5000, 20)), S.batch_row(_b("B2", "2026-11-17", 1, 1))]
    rows = S.merge_rows(old, new)
    assert [r["batch_id"] for r in rows] == ["B1", "B2"]
    assert rows[1]["packs_total"] == 5000  # the stored row wins; history is not rewritten


def test_columns_round_trip() -> None:
    rows = [S.batch_row(_b("B1", "2026-11-17", 5000, 20))]
    assert S.columns_to_rows(S.rows_to_columns(rows)) == rows


def test_daily_kpis_hold_inputs_never_a_percentage() -> None:
    rows = [S.batch_row(b) for b in (_b("B1", "2026-11-16", 5000, 20),
                                     _b("B2", "2026-11-16", 5000, 300, verdict="HOLD"),
                                     _b("B3", "2026-11-17", 4000, 40))]
    qua = {c["name"]: c for c in S.daily_kpi(rows, "KPI-QUA-001")}
    assert qua["periode"]["values"] == ["2026-11-16", "2026-11-17"]
    assert qua["units_defective"]["values"] == [320.0, 40.0]
    assert qua["units_inspected"]["values"] == [10000.0, 4000.0]
    assert qua["units_defective"]["rol"] == "teller" and qua["periode"]["kpi_id"] == "KPI-QUA-001"
    fpy = {c["name"]: c for c in S.daily_kpi(rows, "KPI-PRD-005")}
    assert fpy["units_first_pass"]["values"] == [4980.0, 3960.0]  # the HOLD batch counts as not first pass
    assert fpy["units_produced"]["values"] == [10000.0, 4000.0]


class FakeAios:
    def __init__(self) -> None:
        self.sheets: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def call(self, method: str, path: str, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.endswith("/worksheets"):
            return [{"id": s["id"], "name": n} for n, s in self.sheets.items()]
        if method == "GET":
            sid = path.rsplit("/", 1)[1]
            s = next(s for s in self.sheets.values() if s["id"] == sid)
            return {"data": s["columns"], "version": s["version"]}
        if method == "POST":
            self.sheets[body["name"]] = {"id": f"id{len(self.sheets)}", "columns": body["columns"], "version": 1}
            return {}
        if method == "PUT":
            s = self.sheets[body["name"]]
            assert body["version"] == s["version"]
            s.update(columns=body["columns"], version=s["version"] + 1)
            return {}
        raise AssertionError(method)


class FakeEngine:
    def __init__(self, batches: list[dict]) -> None:
        self.batches = {b["batch_id"]: b for b in batches}

    def call(self, method: str, path: str, body=None):
        if path.startswith("/batches?"):
            return [{"batch_id": i, "state": "COMPLETE"} for i in self.batches]
        return self.batches[path.rsplit("/", 1)[1]]


def test_sync_creates_then_appends_and_survives_engine_pruning() -> None:
    aios = FakeAios()
    eng = FakeEngine([_b("B1", "2026-11-16", 5000, 20)])
    sync = S.Sync(eng, aios, "ws", ["L1"])
    rep = sync.run_once()
    assert rep["Vla L1 · batches"] == "created, 1 new"
    # The engine pruned B1 and made B2: the history in aios keeps both.
    sync.engine = FakeEngine([_b("B2", "2026-11-17", 5000, 10)])
    rep = sync.run_once()
    assert rep["Vla L1 · batches"] == "updated to v2, 1 new"
    rows = S.columns_to_rows(aios.sheets["Vla L1 · batches"]["columns"])
    assert [r["batch_id"] for r in rows] == ["B1", "B2"]
    assert sync.run_once()["Vla L1 · batches"] == "unchanged, 0 new"


def test_batches_before_sync_from_are_left_out() -> None:
    aios = FakeAios()
    eng = FakeEngine([_b("B1", "2026-07-22", 5000, 0), _b("B2", "2026-11-17", 5000, 10)])
    for b in eng.batches.values():
        b["state"] = "COMPLETE"
    eng.call = lambda m, p, body=None: (
        [{"batch_id": i, "state": "COMPLETE", "completed_at": b["completed_at"]} for i, b in eng.batches.items()]
        if p.startswith("/batches?") else eng.batches[p.rsplit("/", 1)[1]])
    rep = S.Sync(eng, aios, "ws", ["L1"], since="2026-11-17T00:00:00").run_once()
    assert rep["Vla L1 · batches"] == "created, 1 new"


def test_a_column_added_in_aios_survives_the_next_sync() -> None:
    aios = FakeAios()
    sync = S.Sync(FakeEngine([_b("B1", "2026-11-16", 5000, 20)]), aios, "ws", ["L1"])
    sync.run_once()
    sheet = aios.sheets["Vla L1 · batches"]
    sheet["columns"].append({"name": "stage", "type": "text", "values": ["before"]})
    sync.engine = FakeEngine([_b("B1", "2026-11-16", 5000, 20), _b("B2", "2026-11-17", 5000, 10)])
    sync.run_once()
    rows = S.columns_to_rows(aios.sheets["Vla L1 · batches"]["columns"])
    assert [(r["batch_id"], r["stage"]) for r in rows] == [("B1", "before"), ("B2", None)]
