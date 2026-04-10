from fastapi import FastAPI
from pymongo import MongoClient
from pydantic import BaseModel
from typing import Optional
import os

app = FastAPI(title="Industrial Data Platform API", version="1.0.0")

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "idp")

client = MongoClient(MONGO_URL)
db = client[MONGO_DB]

@app.get("/health")
def health():
    return {"status": "ok", "service": "Industrial Data Platform API"}

@app.get("/tags")
def get_tags(limit: int = 100):
    """Get latest tag values from all data sources."""
    docs = list(db.tags.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit))
    return {"data": docs, "count": len(docs)}

@app.get("/tags/{tag_name}")
def get_tag(tag_name: str, limit: int = 100):
    """Get historical values for a specific tag."""
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
    """Write a tag value (for testing/simulation)."""
    from datetime import datetime, timezone
    doc = payload.model_dump()
    doc["timestamp"] = datetime.now(timezone.utc).isoformat()
    db.tags.insert_one(doc)
    return {"status": "written", "tag": payload.tag}
