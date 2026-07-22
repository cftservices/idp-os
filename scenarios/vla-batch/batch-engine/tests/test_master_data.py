import random
import pytest

from vla import model as M
from vla.db import get_db, seed_recipes
from vla.batches import BatchRunner


def make_runner():
    db = get_db(mongo_url=None)
    seed_recipes(db)
    return db, BatchRunner(db, bus=None, rng=random.Random(1))


def test_material_master_fields():
    milk = M.MATERIALS["milk"]
    assert milk.category == "LiquidBase" and milk.density_kg_L == 1.03
    starch = M.MATERIALS["starch"]
    assert starch.whole_bag is True and starch.bag_size_kg == 25.0
    assert M.MATERIALS["cocoa"].tolerance_pos_pct == 1.0
    assert M.FINISHED_GOOD_ID in M.MATERIALS  # finished good in the master (PR-27)


def test_dose_tolerance_comes_from_master():
    r = M.get_recipe("chocolate-vla-1L")
    doses = {d.material_id: d for d in r.scaled_doses(5000)}
    # milk: tol 1.0% -> 5000 +/- 50
    assert doses["milk"].tol_min == 4950.0 and doses["milk"].tol_max == 5050.0
    # cocoa: tol 1.0%/1.0% -> 100 +/- 1
    assert doses["cocoa"].tol_min == 99.0 and doses["cocoa"].tol_max == 101.0


def test_release_gate_blocks_unreleased_recipe():
    db, runner = make_runner()
    db.dw_recipes.update_one({"recipe_id": "chocolate-vla-1L"},
                             {"$set": {"status": "draft"}})
    with pytest.raises(ValueError, match="not released"):
        runner.create_batch("chocolate-vla-1L", planned_L=5000)


def test_released_recipe_still_creates_batch():
    db, runner = make_runner()
    b = runner.create_batch("chocolate-vla-1L", planned_L=5000)
    assert b["state"] == "IDLE"
    row = db.dw_doses.find_one({"batch_id": b["batch_id"], "material_id": "milk"})
    assert row["tol_pos_pct"] == 1.0 and row["staged"] == [] and row["qty_prepared"] == 0.0
