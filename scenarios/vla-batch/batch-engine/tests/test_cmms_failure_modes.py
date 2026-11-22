"""Faalmodi en reparatiekwaliteit, de bron van KPI-MNT-003 (herhaalstoringen).

Aanleiding (21-11-2026): met twee faalmodi per rol en een storing per lijn elke 45 minuten
kwam MNT-003 rond 60 tot 77 % uit, tegen een doel van 10 %. Elke machine valt binnen het
herhaalvenster gemiddeld drie keer uit, en dezelfde oorzaak kon gewoon terugkomen. In een
echte fabriek komt een storing terug omdat de reparatie een noodoplossing was. Daarom:
acht faalmodi per rol in een Pareto-verdeling, en een afgeronde storing krijgt `fix`:
`root_cause` (de oorzaak blijft op die machine een tijd weg) of `workaround`.
"""
import random
from datetime import datetime, timedelta, timezone

from vla import cmms as C
from vla.cmms import CmmsConnector
from vla.db import get_db, seed_recipes
from vla.equipment import EquipmentMonitor


def _setup(seed=7, workaround_p=0.4):
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    return db, mon, CmmsConnector(db, mon, rng=random.Random(seed), workaround_p=workaround_p)


def test_every_role_has_eight_weighted_modes_most_common_first():
    for role, modes in C.FAILURE_MODES.items():
        assert len(modes) >= 8, role
        causes = [m[0] for m in modes]
        assert len(set(causes)) == len(causes), role
        weights = [m[3] for m in modes]
        assert all(w > 0 for w in weights), role
        assert weights == sorted(weights, reverse=True), role


def test_no_mode_reuses_the_steam_valve_scenario_cause():
    # scripts/scenario_steam_valve.py speelt een hangende stoomregelklep na; dezelfde tekst
    # als storing vertroebelt de 8D.
    for modes in C.FAILURE_MODES.values():
        for cause, *_ in modes:
            assert "stoomregelklep" not in cause.lower()


def _done_breakdown(db, eq, cause, fix, ago_s):
    t = (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat()
    db.dw_work_orders.insert_one({
        "wo_id": f"WO-{cause[:6]}-{fix}", "equipment_id": eq, "kind": "breakdown",
        "cause": cause, "status": "done", "reported_at": t, "done_at": t, "fix": fix,
        "history": []})


def test_root_cause_fix_keeps_that_cause_away_from_that_machine():
    db, mon, cmms = _setup()
    eq = "cook-unit-01"
    fixed = C.FAILURE_MODES["cook"][0][0]   # de meest voorkomende, dus de zwaarste proef
    _done_breakdown(db, eq, fixed, "root_cause", ago_s=60)
    causes = set()
    for _ in range(200):
        wo = cmms.report_breakdown(eq, repair_s=1)
        causes.add(wo["cause"])
        db.dw_work_orders.delete_many({"wo_id": wo["wo_id"]})
        mon.clear_down(eq)
    assert len(causes) == 7          # alle andere modi kwamen langs, dus elke ronde was nieuw
    assert fixed not in causes


def test_a_workaround_lets_the_same_cause_come_back():
    db, mon, cmms = _setup()
    eq = "cook-unit-01"
    patched = C.FAILURE_MODES["cook"][0][0]
    _done_breakdown(db, eq, patched, "workaround", ago_s=60)
    causes = []
    for _ in range(200):
        wo = cmms.report_breakdown(eq, repair_s=1)
        causes.append(wo["cause"])
        db.dw_work_orders.delete_many({"wo_id": wo["wo_id"]})
        mon.clear_down(eq)
    assert patched in causes


def test_root_cause_fix_holds_only_for_a_limited_time():
    db, mon, cmms = _setup()
    eq = "cook-unit-01"
    fixed = C.FAILURE_MODES["cook"][0][0]
    hold_wall_s = C.FIX_HOLDS_H * 3600 / C._time_scale()
    _done_breakdown(db, eq, fixed, "root_cause", ago_s=hold_wall_s + 60)
    causes = []
    for _ in range(200):
        wo = cmms.report_breakdown(eq, repair_s=1)
        causes.append(wo["cause"])
        db.dw_work_orders.delete_many({"wo_id": wo["wo_id"]})
        mon.clear_down(eq)
    assert fixed in causes


def test_completed_breakdowns_record_the_repair_quality():
    db, mon, cmms = _setup(seed=3, workaround_p=0.4)
    fixes = []
    for i in range(300):
        wo = cmms.report_breakdown("filler-01", repair_s=1)
        t0 = datetime.fromisoformat(wo["reported_at"])
        cmms.tick(t0 + timedelta(seconds=25))
        cmms.tick(t0 + timedelta(seconds=55))
        started = datetime.fromisoformat(cmms.list(equipment_id="filler-01")[0]["started_at"])
        cmms.tick(started + timedelta(seconds=2))
        done = cmms.list(equipment_id="filler-01")[0]
        assert done["status"] == "done"
        fixes.append(done["fix"])
        db.dw_work_orders.delete_many({})   # elke ronde een schone machine
    assert set(fixes) == {"root_cause", "workaround"}
    share = fixes.count("workaround") / len(fixes)
    assert 0.3 < share < 0.5


def test_cbm_work_orders_get_no_repair_quality():
    db, mon, cmms = _setup()
    wo = cmms.create("cook-unit-01", "cbm", "Vervuiling", priority="P3", source_ref="X")
    cmms.tick()
    assert "fix" not in cmms.list(equipment_id="cook-unit-01")[0]
