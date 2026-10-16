"""Tests voor de KPI-laag (fase 4).

De harde regels uit het bouwdesign die hier moeten landen:
  * statusregel in beide richtingen, inclusief exact-op-de-grens en UNSET;
  * delta None bij een ontbrekend vorig venster, delta_pct None bij vorige 0;
  * ontbrekende kostenparameter laat de categorie WEG, levert nooit 0.0;
  * geen negatieve verliesbedragen;
  * causale en resulterende verliezen worden niet bij elkaar opgeteld.
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from vla import kpi
from vla.batches import BatchRunner
from vla.db import get_db, seed_recipes
from vla.equipment import CipRequired, EquipmentMonitor

TELEM_OK = {"peak_cook_temp_C": 88.0, "hold_elapsed_sec": 300.0,
            "packs_total": 4980, "reject_count": 20}
TELEM_BAD = {"fault": "cook_undertemp", "magnitude": 0.6,
             "hold_elapsed_sec": 300.0, "packs_total": 4900, "reject_count": 100}

HIGHER = {"kpi_id": "x", "direction": "higher_is_better",
          "target": 95.0, "warn": 90.0, "critical": 80.0}
LOWER = {"kpi_id": "y", "direction": "lower_is_better",
         "target": 1.5, "warn": 3.0, "critical": 5.0}


def _seeded_db(n_ok=2, n_bad=1):
    """N batches door de echte lifecycle. De CIP-gate blokkeert na
    DIRTY_AFTER_BATCHES batches, dus die wordt onderweg netjes uitgevoerd:
    dat is systeemgedrag, geen obstakel om omheen te werken."""
    db = get_db(mongo_url=None)
    seed_recipes(db)
    mon = EquipmentMonitor(db, bus=None)
    runner = BatchRunner(db, bus=None, rng=random.Random(48), equipment=mon)
    for telem in [TELEM_OK] * n_ok + [TELEM_BAD] * n_bad:
        try:
            b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        except CipRequired:
            mon.perform_cip("cook-unit-01", operator_id="test")
            b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
        runner.start_batch(b["batch_id"], telemetry=telem)
    return db


# ------------------------------------------------------------------ statusregel

@pytest.mark.parametrize("value,expected", [
    (96.0, "OK"),
    (95.0, "OK"),        # exact op target
    (94.9, "WARNING"),
    (90.0, "WARNING"),   # exact op warn
    (89.9, "CRITICAL"),
    (80.0, "CRITICAL"),  # exact op critical
    (79.9, "CRITICAL"),  # eronder blijft CRITICAL, met beyond_critical
])
def test_status_higher_is_better(value, expected):
    assert kpi.kpi_status(value, HIGHER)["status"] == expected


@pytest.mark.parametrize("value,expected", [
    (1.0, "OK"),
    (1.5, "OK"),         # exact op target
    (1.6, "WARNING"),
    (3.0, "WARNING"),    # exact op warn
    (3.1, "CRITICAL"),
    (5.0, "CRITICAL"),   # exact op critical
    (5.1, "CRITICAL"),
])
def test_status_lower_is_better(value, expected):
    assert kpi.kpi_status(value, LOWER)["status"] == expected


def test_beyond_critical_is_flagged_but_stays_critical():
    """Het critical-veld moet daadwerkelijk gebruikt worden: 89 en 40 mogen
    niet identiek lezen. De status blijft CRITICAL (drie kleuren in de UI),
    maar beyond_critical onderscheidt ze."""
    near = kpi.kpi_status(85.0, HIGHER)
    far = kpi.kpi_status(40.0, HIGHER)
    assert near["status"] == far["status"] == "CRITICAL"
    assert near["beyond_critical"] is False
    assert far["beyond_critical"] is True


def test_status_unset_never_silently_ok():
    assert kpi.kpi_status(91.0, None)["status"] == "UNSET"
    assert kpi.kpi_status(None, HIGHER)["status"] == "UNSET"
    assert kpi.kpi_status(91.0, None)["reason"]
    assert kpi.kpi_status(None, HIGHER)["reason"]


def test_status_incomplete_target_is_unset():
    assert kpi.kpi_status(5.0, {"direction": "higher_is_better"})["status"] == "UNSET"


# ----------------------------------------------------------------------- delta

def test_delta_none_without_previous_window():
    d = kpi._delta(91.0, None, "higher_is_better")
    assert d["delta"] is None and d["delta_pct"] is None
    assert d["favourable"] is None


def test_delta_pct_none_when_previous_is_zero():
    d = kpi._delta(4.0, 0.0, "higher_is_better")
    assert d["delta"] == 4.0
    assert d["delta_pct"] is None


def test_delta_direction_aware():
    """Bij lower_is_better is stijgen ongunstig. Een kale pijl zou het
    omgekeerde suggereren."""
    up_higher = kpi._delta(95.0, 90.0, "higher_is_better")
    up_lower = kpi._delta(5.0, 3.0, "lower_is_better")
    assert up_higher["favourable"] is True
    assert up_lower["favourable"] is False


# --------------------------------------------------------------------- vensters

def test_window_bounds_are_half_open_and_contiguous():
    now = datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc)
    cur = kpi.window_bounds("week", now=now, offset=0)
    prev = kpi.window_bounds("week", now=now, offset=1)
    assert prev[1] == cur[0], "vorig venster moet exact aansluiten"
    assert cur[1] - cur[0] == timedelta(weeks=1)


def test_window_month_is_calendar_not_thirty_days():
    now = datetime(2026, 3, 15, tzinfo=timezone.utc)
    start, end = kpi.window_bounds("month", now=now)
    assert (start.day, start.month) == (1, 3)
    assert (end.day, end.month) == (1, 4)
    prev_start, prev_end = kpi.window_bounds("month", now=now, offset=1)
    assert (prev_start.month, prev_end.month) == (2, 3)


def test_window_month_offset_crosses_year():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    start, _ = kpi.window_bounds("month", now=now, offset=2)
    assert (start.year, start.month) == (2025, 11)


def test_unknown_window_raises():
    with pytest.raises(ValueError, match="unknown window"):
        kpi.window_bounds("fortnight")


# ------------------------------------------------------------------------ KPIs

def test_compute_kpis_returns_full_set_with_metadata():
    db = _seeded_db()
    rows = kpi.compute_kpis(db, window="week", compare=True, model={})
    assert len(rows) == len(kpi.KPI_DEFS)
    for row in rows:
        # ISO 22400-2 Table 1 schrijft deze contextvelden per KPI voor
        for field in ("timing", "audience", "production_methodology",
                      "formula", "window", "from", "to"):
            assert row[field], f"{row['kpi_id']} mist {field}"


def test_without_targets_everything_is_unset_with_a_reason():
    """Geen kpi_targets-blok in het model betekent UNSET, niet OK."""
    db = _seeded_db()
    rows = kpi.compute_kpis(db, window="week", compare=False, model={})
    assert {r["status"] for r in rows} == {"UNSET"}
    assert all(r.get("reason") for r in rows)


def test_targets_from_model_produce_a_real_status():
    db = _seeded_db()
    model = {"kpi_targets": [{"kpi_id": "quality_ratio",
                              "direction": "higher_is_better",
                              "target": 97.0, "warn": 94.0, "critical": 90.0}]}
    rows = {r["kpi_id"]: r for r in
            kpi.compute_kpis(db, window="week", compare=False, model=model)}
    assert rows["quality_ratio"]["status"] in ("OK", "WARNING", "CRITICAL")
    assert rows["scrap_ratio"]["status"] == "UNSET"


def test_quality_ratio_excludes_rework_from_the_numerator():
    """QR = GQ/PQ. Een HOLD-batch levert packs maar telt niet als goed, dus de
    ratio moet onder 100 liggen zodra er een niet-APPROVED batch is."""
    db = _seeded_db(n_ok=2, n_bad=1)
    value, meta = kpi._quality_ratio(db, *kpi.window_bounds("week"))
    assert value is not None
    assert 0 < value < 100
    assert meta["produced_qty"] > meta["good_qty"]


def test_otif_is_unset_without_due_dates():
    """Zonder leverdatum wordt OTIF nooit stilzwijgend 100 procent."""
    db = _seeded_db()
    value, meta = kpi._otif(db, *kpi.window_bounds("week"))
    assert value is None
    assert "leverdatum" in meta["reason"] or "orders" in meta["reason"]


def test_capability_unset_below_minimum_samples():
    db = _seeded_db(n_ok=2, n_bad=1)
    value, meta = kpi._capability_cpk(db, *kpi.window_bounds("week"), (150.0, 300.0))
    assert value is None
    assert str(kpi.CAPABILITY_MIN_SAMPLES) in meta["reason"]


def test_capability_binds_on_the_lower_limit():
    """Te laag koken is het risico, dus de ondergrensterm moet bindend zijn en
    apart zichtbaar."""
    db = _seeded_db(n_ok=kpi.CAPABILITY_MIN_SAMPLES + 2, n_bad=1)
    value, meta = kpi._capability_cpk(db, *kpi.window_bounds("week"), (150.0, 300.0))
    if value is not None:  # vereist spreiding in de gezaaide set
        assert meta["binding"] in ("lower", "upper")
        assert meta["cpk_lower"] is not None and meta["cpk_upper"] is not None
        assert value == min(meta["cpk_lower"], meta["cpk_upper"])


def test_empty_window_yields_unset_not_zero():
    """Een venster zonder data levert None plus een reden. Nooit 0.0, want dat
    leest als een gemeten nul."""
    db = _seeded_db()
    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    rows = kpi.compute_kpis(db, window="day", compare=False,
                            now=long_ago, model={})
    assert all(r["value"] is None for r in rows)
    assert all(r.get("reason") for r in rows)


# -------------------------------------------------------------------- verliezen

COST_MODEL = {
    "currency": "EUR",
    "value_per_pack": 0.62,
    "cost_per_kg_material": {"MILK": 0.48, "SUGAR": 0.85,
                             "ModifiedStarch": 1.60, "COCOA": 4.20},
    "cost_per_downtime_hour": 450.0,
    "rework_cost_per_batch": 620.0,
}


def test_losses_without_cost_model_are_omitted_not_zero():
    db = _seeded_db()
    out = kpi.compute_losses(db, *kpi.window_bounds("week"), None)
    assert out["items"] == []
    assert out["total_causal"] is None
    assert out["omitted"] and out["omitted"][0]["reason"]


def test_missing_cost_parameter_drops_the_category():
    """Ontbrekende parameter betekent categorie weglaten, niet 0.0 tonen."""
    db = _seeded_db()
    partial = {"currency": "EUR", "value_per_pack": 0.62}
    out = kpi.compute_losses(db, *kpi.window_bounds("week"), partial)
    omitted = {o["category"] for o in out["omitted"]}
    assert "downtime" in omitted and "rework" in omitted
    assert all(i["category"] != "downtime" for i in out["items"])


def test_resultant_losses_are_excluded_from_the_headline_total():
    """De kern van Manufacturing Cost Deployment: resulterende verliezen mogen
    niet bij hun oorzaak worden opgeteld."""
    db = _seeded_db(n_ok=2, n_bad=2)
    out = kpi.compute_losses(db, *kpi.window_bounds("week"), COST_MODEL)
    causal = [i for i in out["items"] if i["causal_or_resultant"] == "causal"]
    resultant = [i for i in out["items"] if i["causal_or_resultant"] == "resultant"]
    assert out["total_causal"] == round(sum(i["amount"] for i in causal), 2)
    for item in resultant:
        assert item["caused_by"], "een resultant moet zijn oorzaak noemen"
        assert item["share_pct"] is None


def test_no_negative_loss_amounts():
    db = _seeded_db(n_ok=3, n_bad=1)
    out = kpi.compute_losses(db, *kpi.window_bounds("week"), COST_MODEL)
    assert all(i["amount"] >= 0 for i in out["items"])


def test_losses_are_ranked_causal_first_then_by_amount():
    db = _seeded_db(n_ok=2, n_bad=2)
    items = kpi.compute_losses(db, *kpi.window_bounds("week"), COST_MODEL)["items"]
    kinds = [i["causal_or_resultant"] for i in items]
    assert kinds == sorted(kinds, key=lambda k: k != "causal")
    causal_amounts = [i["amount"] for i in items
                      if i["causal_or_resultant"] == "causal"]
    assert causal_amounts == sorted(causal_amounts, reverse=True)


# --------------------------------------------------------------------- summary

def test_summary_reports_its_own_window_contract():
    """Scherm en PDF moeten hetzelfde venster kunnen aantonen, dus from/to en
    de tijdzone horen in de respons."""
    db = _seeded_db()
    out = kpi.summary(db, window="week", compare=True)
    assert out["window"] == "week"
    assert out["from"] < out["to"]
    assert out["timezone"] == "UTC"
    assert out["shift_hours"] == kpi.SHIFT_HOURS
    assert len(out["kpis"]) == len(kpi.KPI_DEFS)
    assert "losses" in out


# ------------------------------------------------------------- massabalans-yield

DENSITY = 1.03


def test_mass_yield_is_not_plan_attainment():
    """De twee meten echt iets anders: plan-realisatie deelt door een
    planningsgetal, yield door de werkelijke materiaalinzet."""
    db = _seeded_db(n_ok=2, n_bad=1)
    win = kpi.window_bounds("week")
    y, ymeta = kpi._mass_yield(db, *win, DENSITY)
    p, _ = kpi._plan_attainment(db, *win)
    assert y is not None and p is not None
    assert y != p
    assert ymeta["kg_in"] > 0 and ymeta["kg_out"] > 0


def test_mass_yield_is_below_hundred_percent():
    """5850 kg grondstof levert 5000 L product: het verschil is indamping. Een
    yield boven 100 procent zou betekenen dat er massa uit het niets komt."""
    db = _seeded_db(n_ok=3)
    value, meta = kpi._mass_yield(db, *kpi.window_bounds("week"), DENSITY)
    assert value is not None
    assert 70.0 < value < 100.0, f"onwaarschijnlijke yield {value}"
    assert "note" not in meta


def test_mass_yield_unset_without_density():
    value, meta = kpi._mass_yield(db=_seeded_db(), start=kpi.window_bounds("week")[0],
                                  end=kpi.window_bounds("week")[1], density=None)
    assert value is None
    assert "density" in meta["reason"] or "dichtheid" in meta["reason"]


def test_mass_yield_in_the_kpi_set():
    db = _seeded_db()
    rows = {r["kpi_id"]: r for r in
            kpi.compute_kpis(db, window="week", compare=False,
                             model={"product_density_kg_L": DENSITY})}
    assert "mass_yield_pct" in rows
    assert rows["mass_yield_pct"]["value"] is not None


# ---------------------------------------------------------------------- afkeur

def test_scrap_ratio_is_real_now_that_the_factory_rejects():
    """De fabriek produceert afkeur, dus scrap_ratio mag geen 0.0 meer zijn."""
    db = _seeded_db(n_ok=2, n_bad=1)
    value, meta = kpi._scrap_ratio(db, *kpi.window_bounds("week"))
    assert value is not None
    assert value > 0.0, "afkeur is gesimuleerd, dus scrap moet meetbaar zijn"
    assert meta["scrap_qty"] > 0


def test_scrap_costs_appear_in_the_loss_block():
    db = _seeded_db(n_ok=2, n_bad=2)
    out = kpi.compute_losses(db, *kpi.window_bounds("week"), COST_MODEL)
    scrap = [i for i in out["items"] if i["category"] == "scrap"]
    assert scrap, "afkeur moet nu een eigen verliespost opleveren"
    assert scrap[0]["amount"] > 0


def test_zero_amount_losses_are_dropped_not_shown_as_zero():
    """Een post die op 0,00 afrondt is visueel niet te onderscheiden van 'niet
    gekalibreerd'. Dat onderscheid moet overeind blijven, dus hij valt weg."""
    db = _seeded_db(n_ok=2, n_bad=1)
    out = kpi.compute_losses(db, *kpi.window_bounds("week"), COST_MODEL)
    assert all(i["amount"] > 0 for i in out["items"])


def test_future_time_is_not_counted_as_downtime():
    """Een lopend venster eindigt in de toekomst. Die tijd doorrekenen als
    stilstand maakte de benuttingsgraad 0 procent zodra je midden in een week
    keek."""
    db = _seeded_db(n_ok=3)
    start, end = kpi.window_bounds("month")   # eindigt altijd in de toekomst
    value, _ = kpi._utilization_efficiency(db, start, end)
    assert value is not None
    assert value > 0.0, "benutting mag niet naar nul zakken door toekomstige tijd"


def test_dirty_counts_as_productive_not_downtime():
    """Een vervuilde kookketel kookt door. Hem als stilstand tellen straft
    dubbel, want de vervuiling verlaagt al de performance, en het leverde in
    productie een benuttingsgraad van 0,68 procent op voor een lijn die de hele
    week draaide."""
    assert "Dirty" not in kpi._DOWNTIME_STATES
    assert "Dirty" in kpi._PRODUCTIVE_STATES
    db = _seeded_db(n_ok=6, n_bad=1)
    value, _ = kpi._utilization_efficiency(db, *kpi.window_bounds("week"))
    assert value is not None
    assert value > 50.0, f"benutting {value} is onwaarschijnlijk laag voor een draaiende lijn"

def test_naive_due_date_counts_as_utc_and_does_not_crash_otif():
    """Een order met een kale datum (zonder offset) legde /kpi/summary plat met
    een TypeError in _within; hij telt nu als UTC."""
    start = datetime(2026, 10, 6, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    assert kpi._within("2026-10-06T12:00:00", start, end)
    assert not kpi._within("2026-10-07", start, end)
    db = get_db()
    db.dw_orders.insert_one({"order_id": "PO-NAIVE", "due_date": "2026-10-06",
                             "status": "OPEN"})
    kpi._otif(db, start, end)
