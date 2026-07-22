"""Staleness gate on the bus tag-cache (E4 finding).

On the VPS a dead OPC-UA ingest left only retained MQTT replays in the cache;
the instant-mode fallback then read those stale values (20 C / 0 s) instead of
fabricating healthy ones, wrongly REJECTING batches. The fix: retained
deliveries carry no freshness stamp, and age-gated reads return None for them.
"""

import random
import types

from vla import model as M
from vla.bus import VlaBus
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner


def _msg(topic: str, payload: str, retain: bool):
    return types.SimpleNamespace(topic=topic, payload=payload.encode(), retain=retain)


def test_live_value_passes_age_gate_retained_does_not():
    bus = VlaBus()
    live_t = M.status_topic("cook-unit-01", "temp_C")
    ret_t = M.status_topic("cook-unit-01", "hold_elapsed_sec")
    bus._on_message(None, None, _msg(live_t, '{"value": 88.0}', retain=False))
    bus._on_message(None, None, _msg(ret_t, '{"value": 0.0}', retain=True))
    # ungated: both readable (backwards compatible)
    assert bus.latest_value("cook-unit-01", "temp_C") == 88.0
    assert bus.latest_value("cook-unit-01", "hold_elapsed_sec") == 0.0
    # age-gated: live value fresh, retained replay counts as stale
    assert bus.latest_value("cook-unit-01", "temp_C", max_age_s=30.0) == 88.0
    assert bus.latest_value("cook-unit-01", "hold_elapsed_sec",
                            max_age_s=30.0) is None


def test_old_live_value_expires():
    bus = VlaBus()
    topic = M.status_topic("filler-01", "packs_total")
    bus._on_message(None, None, _msg(topic, '{"value": 4980}', retain=False))
    bus._rx[topic] -= 60.0  # age the freshness stamp by a minute
    assert bus.latest_value("filler-01", "packs_total") == 4980
    assert bus.latest_value("filler-01", "packs_total", max_age_s=30.0) is None


def test_instant_fallback_fabricates_healthy_on_retained_only_cache():
    """Reproduces the VPS bug: cache full of retained idle values (20 C, 0 s,
    30 cP) with a dead subscription. The fallback must fabricate a healthy
    batch (APPROVED), not book the stale idle readings (REJECTED at 30 cP)."""
    bus = VlaBus()  # never started -> connected False -> instant mode
    for tag, val in [("temp_C", 20.0), ("hold_elapsed_sec", 0.0),
                     ("viscosity_cP", 30.0)]:
        bus._on_message(None, None, _msg(
            M.status_topic("cook-unit-01", tag),
            '{"value": %s}' % val, retain=True))
    bus._on_message(None, None, _msg(
        M.status_topic("filler-01", "packs_total"), '{"value": 12}', retain=True))

    db = get_db(mongo_url=None)
    seed_recipes(db)
    runner = BatchRunner(db, bus=bus, rng=random.Random(60))
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    res = runner.start_batch(b["batch_id"])  # no telemetry -> fallback paths
    assert res["verdict"] == "APPROVED"
    assert res["end_viscosity_cP"] >= 150.0
    assert res["packs_total"] > 1000  # fabricated near planned_L, not stale 12
