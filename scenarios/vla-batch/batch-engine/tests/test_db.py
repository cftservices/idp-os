"""dw_* collection contract (05-Backend §3)."""
from vla.db import get_db, seed_recipes, COLLECTIONS

EXPECTED = [
    "dw_batches", "dw_recipes", "dw_materials", "dw_doses", "dw_production",
    "dw_samples", "dw_batch_events", "dw_alarms", "dw_orders", "dw_equipment_state",
    "dw_handling_units",
]


def test_collections_are_dw_prefixed():
    assert COLLECTIONS == EXPECTED


def test_attribute_access_and_seed():
    db = get_db(mongo_url=None)  # force in-memory
    seed_recipes(db)
    assert db.backend == "memory"
    assert db.dw_recipes.count_documents({}) >= 1
    assert db.dw_orders.count_documents({}) == 0
    assert db.dw_equipment_state.count_documents({}) == 0
