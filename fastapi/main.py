from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pymongo import MongoClient
from pydantic import BaseModel
import os

app = FastAPI(title="Industrial Data Platform API", version="1.1.0")

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "idp")

client = MongoClient(MONGO_URL)
db = client[MONGO_DB]


# MonsterMQ archive groups write each MQTT message as one document
# into a collection named after the archive group:
#   dairy_data    → "DairyPlant/#"
#   bakery_data   → "bakery-works-utrecht/#"
#   plc_data      → "idp/#"
#
# Document shape (MonsterMQ MongoDB store):
#   {topic: str, payload: str|bytes, qos: int, retained: bool, time: ISODate}
# Different MonsterMQ versions vary slightly; we tolerate `time` OR
# `timestamp` for the time field and decode payloads as numbers when
# possible (else return as string).
ARCHIVE_COLLECTIONS = {
    "plc": "plc_data",
    "dairy": "dairy_data",
    "bakery": "bakery_data",
}


@app.get("/")
def root():
    return {"status": "ok", "service": "Industrial Data Platform API"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "Industrial Data Platform API"}


@app.get("/tags")
def get_tags(limit: int = 100):
    """Get latest tag values from the legacy `tags` collection."""
    docs = list(db.tags.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
    return {"data": docs, "count": len(docs)}


@app.get("/tags/{tag_name}")
def get_tag(tag_name: str, limit: int = 100):
    """Get historical values for a single tag from the legacy `tags` collection."""
    docs = list(db.tags.find(
        {"tag": tag_name}, {"_id": 0}
    ).sort("timestamp", -1).limit(limit))
    return {"tag": tag_name, "data": docs, "count": len(docs)}


class TagValue(BaseModel):
    tag: str
    value: float
    unit: Optional[str] = None
    source: Optional[str] = None


@app.post("/tags")
def post_tag(payload: TagValue):
    """Write a tag value (for testing / simulation)."""
    doc = payload.model_dump()
    doc["timestamp"] = datetime.now(timezone.utc).isoformat()
    db.tags.insert_one(doc)
    return {"status": "written", "tag": payload.tag}


# ─────────────────────────────────────────────────────────────────────────────
# Archive endpoints — query MonsterMQ-written collections directly
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_collection(scope: str):
    name = ARCHIVE_COLLECTIONS.get(scope)
    if not name:
        raise HTTPException(404, f"Unknown scope {scope!r}. Use one of: {list(ARCHIVE_COLLECTIONS)}")
    return db[name]


def _time_field(doc: dict) -> Optional[datetime]:
    for key in ("time", "timestamp", "ts"):
        v = doc.get(key)
        if v is None:
            continue
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _coerce_value(payload):
    """Best-effort: numbers as float, booleans as 0/1, else original string."""
    if payload is None:
        return None
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(payload, (int, float)):
        return float(payload)
    if isinstance(payload, bool):
        return 1.0 if payload else 0.0
    s = str(payload).strip()
    if s.lower() in ("true", "false"):
        return 1.0 if s.lower() == "true" else 0.0
    try:
        return float(s)
    except ValueError:
        return s  # leave as string


@app.get("/archive/{scope}/topics")
def list_topics(scope: str, prefix: Optional[str] = None, limit: int = 500):
    """List distinct topics in the archive collection, optionally filtered by prefix."""
    coll = _resolve_collection(scope)
    match = {}
    if prefix:
        match["topic"] = {"$regex": f"^{prefix}"}
    topics = sorted(coll.distinct("topic", match))
    return {"data": [{"topic": t} for t in topics[:limit]], "count": min(len(topics), limit)}


@app.get("/archive/{scope}/latest")
def latest(scope: str, topic: str = Query(..., description="Exact topic to look up")):
    """Latest value + timestamp for a single topic."""
    coll = _resolve_collection(scope)
    doc = coll.find_one({"topic": topic}, sort=[("time", -1), ("timestamp", -1)])
    if doc is None:
        return {"topic": topic, "value": None, "time": None}
    t = _time_field(doc)
    return {
        "topic": topic,
        "value": _coerce_value(doc.get("payload")),
        "time": t.isoformat() if t else None,
    }


@app.get("/archive/{scope}/history")
def history(
    scope: str,
    topic: str = Query(..., description="Exact topic to fetch"),
    minutes: int = Query(15, ge=1, le=1440),
    limit: int = Query(1000, ge=1, le=10000),
):
    """Time-series points for a topic over the last N minutes (numeric only)."""
    coll = _resolve_collection(scope)
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    # MonsterMQ may use `time` or `timestamp` — try both
    cur = coll.find(
        {
            "topic": topic,
            "$or": [{"time": {"$gte": since}}, {"timestamp": {"$gte": since}}],
        },
        {"_id": 0},
    ).sort([("time", 1), ("timestamp", 1)]).limit(limit)
    out = []
    for d in cur:
        t = _time_field(d)
        v = _coerce_value(d.get("payload"))
        if t is None or not isinstance(v, (int, float)):
            continue
        out.append({"time": t.isoformat(), "value": v})
    return {"topic": topic, "data": out, "count": len(out)}


@app.get("/archive/{scope}/snapshot")
def snapshot(scope: str, prefix: str = Query(..., description="Topic prefix, e.g. DairyPlant/")):
    """Latest value for every topic matching a prefix (one row per topic).

    Useful for instrument-grid panels in Grafana where each topic = one stat.
    """
    coll = _resolve_collection(scope)
    pipeline = [
        {"$match": {"topic": {"$regex": f"^{prefix}"}}},
        {"$sort": {"time": -1, "timestamp": -1}},
        {"$group": {
            "_id": "$topic",
            "payload": {"$first": "$payload"},
            "time": {"$first": "$time"},
            "timestamp": {"$first": "$timestamp"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = []
    for doc in coll.aggregate(pipeline):
        t = _time_field({"time": doc.get("time"), "timestamp": doc.get("timestamp")})
        rows.append({
            "topic": doc["_id"],
            "value": _coerce_value(doc.get("payload")),
            "time": t.isoformat() if t else None,
        })
    return {"prefix": prefix, "data": rows, "count": len(rows)}
