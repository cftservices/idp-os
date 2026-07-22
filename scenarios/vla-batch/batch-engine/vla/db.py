"""Persistence layer: Mongo backend + in-memory fallback (offline-first).

If MONGO_URL is set and pymongo can connect, collections are Mongo-backed
(db from MONGO_DB, default 'idp'); otherwise an in-memory dict-of-lists store
with the same minimal API is used so demos and tests run fully offline.

Domain collections (§05-Backend §3):
  dw_batches, dw_recipes, dw_materials, dw_doses, dw_production,
  dw_samples, dw_batch_events, dw_alarms, dw_orders, dw_equipment_state
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

log = logging.getLogger("vla.db")

COLLECTIONS = [
    "dw_batches",
    "dw_recipes",
    "dw_materials",
    "dw_doses",
    "dw_production",
    "dw_samples",
    "dw_batch_events",
    "dw_alarms",
    "dw_orders",
    "dw_equipment_state",
]


def _matches(doc: dict, query: dict) -> bool:
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


def _strip_id(doc: dict) -> dict:
    return {k: val for k, val in doc.items() if k != "_id"}


class _MemCollection:
    """Minimal in-memory collection mirroring the subset of pymongo we use."""

    def __init__(self, name: str):
        self.name = name
        self._docs: list[dict] = []

    def insert_one(self, doc: dict) -> None:
        self._docs.append(dict(doc))

    def insert_many(self, docs: list[dict]) -> None:
        for d in docs:
            self.insert_one(d)

    def find(self, query: Optional[dict] = None) -> list[dict]:
        query = query or {}
        return [_strip_id(d) for d in self._docs if _matches(d, query)]

    def find_one(self, query: Optional[dict] = None) -> Optional[dict]:
        query = query or {}
        for d in self._docs:
            if _matches(d, query):
                return _strip_id(d)
        return None

    def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        set_fields = update.get("$set", update)
        for d in self._docs:
            if _matches(d, query):
                d.update(set_fields)
                return
        if upsert:
            new = dict(query)
            new.update(set_fields)
            self._docs.append(new)

    def count_documents(self, query: Optional[dict] = None) -> int:
        return len(self.find(query))

    def delete_many(self, query: Optional[dict] = None) -> None:
        query = query or {}
        self._docs = [d for d in self._docs if not _matches(d, query)]


class _MongoCollectionWrapper:
    """Wraps a pymongo collection, stripping _id on reads for clean JSON."""

    def __init__(self, coll):
        self._coll = coll

    def insert_one(self, doc: dict) -> None:
        self._coll.insert_one(dict(doc))

    def insert_many(self, docs: list[dict]) -> None:
        if docs:
            self._coll.insert_many([dict(d) for d in docs])

    def find(self, query: Optional[dict] = None) -> list[dict]:
        return [_strip_id(d) for d in self._coll.find(query or {})]

    def find_one(self, query: Optional[dict] = None) -> Optional[dict]:
        d = self._coll.find_one(query or {})
        return _strip_id(d) if d else None

    def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        if any(k.startswith("$") for k in update):
            self._coll.update_one(query, update, upsert=upsert)
        else:
            self._coll.update_one(query, {"$set": update}, upsert=upsert)

    def count_documents(self, query: Optional[dict] = None) -> int:
        return self._coll.count_documents(query or {})

    def delete_many(self, query: Optional[dict] = None) -> None:
        self._coll.delete_many(query or {})


class Database:
    """Facade over the collection map; attribute access returns collections."""

    def __init__(self, backend: str, collections: dict[str, Any]):
        self.backend = backend  # "mongo" | "memory"
        self._collections = collections

    def collection(self, name: str):
        return self._collections[name]

    def __getattr__(self, name: str):
        cols = self.__dict__.get("_collections", {})
        if name in cols:
            return cols[name]
        raise AttributeError(name)

    def reset(self) -> None:
        for c in self._collections.values():
            c.delete_many({})


def get_db(mongo_url: Optional[str] = None, db_name: Optional[str] = None) -> Database:
    """Return a Database. Uses Mongo when MONGO_URL is set + reachable, else memory."""
    mongo_url = mongo_url if mongo_url is not None else os.environ.get("MONGO_URL")
    db_name = db_name or os.environ.get("MONGO_DB", "idp")
    if mongo_url:
        try:
            import pymongo  # noqa: F401
            from pymongo import MongoClient

            client = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
            client.admin.command("ping")  # fail fast if unreachable
            mdb = client[db_name]
            cols = {name: _MongoCollectionWrapper(mdb[name]) for name in COLLECTIONS}
            log.info("Using Mongo backend at %s (db=%s)", mongo_url, db_name)
            return Database("mongo", cols)
        except Exception as e:  # broad: any connect/import failure -> memory
            log.warning("Mongo unavailable (%s) — falling back to in-memory store", e)

    cols = {name: _MemCollection(name) for name in COLLECTIONS}
    log.info("Using in-memory store (no MONGO_URL or Mongo unreachable)")
    return Database("memory", cols)


def seed_recipes(db: Database) -> None:
    """Persist the recipe + material seed if the collections are empty."""
    from . import model as M

    if db.dw_recipes.count_documents({}) == 0:
        for rid, r in M.RECIPES.items():
            db.dw_recipes.insert_one({
                "recipe_id": r.recipe_id,
                "product_name": r.product_name,
                "basis_L": r.basis_L,
                "doses": [{"material_id": d.material_id, "qty_target": d.qty_target,
                           "uom": d.uom} for d in r.doses],
                "cook_setpoint_C": r.cook_setpoint_C,
                "hold_sec": r.hold_sec,
                "cool_target_C": r.cool_target_C,
                "spec_min_cP": r.spec_min_cP,
                "spec_max_cP": r.spec_max_cP,
                "agitator_rpm": r.agitator_rpm,
            })
    if db.dw_materials.count_documents({}) == 0:
        for mid, m in M.MATERIALS.items():
            db.dw_materials.insert_one({
                "material_id": m.material_id, "name": m.name, "uom": m.uom,
            })
