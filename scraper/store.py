from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta
import chromadb


class LinkedInStore:
    """Local ChromaDB store for LinkedIn posts and connections."""

    def __init__(self, chroma_path: str):
        self.client = chromadb.PersistentClient(path=chroma_path)

        # Use chromadb default embedding (no external deps needed)
        self.posts = self.client.get_or_create_collection(name="posts")
        self.connections = self.client.get_or_create_collection(name="connections")

    # ── Posts ──────────────────────────────────────────────────────────────

    def _post_id(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def add_post(self, post: dict) -> None:
        """Add post to DB, skip silently if URL already exists."""
        post_id = self._post_id(post["url"])
        existing = self.posts.get(ids=[post_id])
        if existing["ids"]:
            return

        metadata = {k: v for k, v in post.items() if k != "text"}
        metadata["keywords_matched"] = json.dumps(metadata.get("keywords_matched", []))
        metadata["reply_drafted"] = bool(metadata.get("reply_drafted", False))

        self.posts.add(
            ids=[post_id],
            documents=[post["text"]],
            metadatas=[metadata],
        )

    def get_recent_posts(self, hours: int = 24) -> list[dict]:
        """Return all posts stored within the last N hours."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        results = self.posts.get(include=["documents", "metadatas"])
        posts = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            if meta.get("timestamp", "") >= cutoff:
                entry = dict(meta)
                entry["text"] = doc
                entry["keywords_matched"] = json.loads(entry.get("keywords_matched", "[]"))
                posts.append(entry)
        return posts

    def get_daily_summary(self) -> dict[str, int]:
        """Count posts by classification for today."""
        today = datetime.now().date().isoformat()
        results = self.posts.get(include=["metadatas"])
        counts: dict[str, int] = {}
        for meta in results["metadatas"]:
            if meta.get("timestamp", "").startswith(today):
                cls = meta.get("classification", "neutral")
                counts[cls] = counts.get(cls, 0) + 1
        return counts

    # ── Connections ────────────────────────────────────────────────────────

    def _connection_id(self, profile_url: str) -> str:
        return hashlib.sha256(profile_url.encode()).hexdigest()[:16]

    def upsert_connection(self, connection: dict) -> None:
        """Insert or update a connection. Preserves existing post_count."""
        conn_id = self._connection_id(connection["profile_url"])
        existing = self.connections.get(ids=[conn_id], include=["metadatas"])

        post_count = 0
        if existing["ids"]:
            post_count = existing["metadatas"][0].get("post_count", 0)
            self.connections.delete(ids=[conn_id])

        doc = f"{connection['name']} {connection['title']} {connection['company']}"
        metadata = {
            "profile_url": connection["profile_url"],
            "name": connection["name"],
            "title": connection["title"],
            "company": connection["company"],
            "classification": connection.get("classification", "unknown"),
            "first_seen": connection.get("first_seen", datetime.now().date().isoformat()),
            "last_seen": datetime.now().date().isoformat(),
            "post_count": post_count,
        }
        self.connections.add(ids=[conn_id], documents=[doc], metadatas=[metadata])

    def get_connection(self, profile_url: str) -> dict | None:
        conn_id = self._connection_id(profile_url)
        result = self.connections.get(ids=[conn_id], include=["documents", "metadatas"])
        if not result["ids"]:
            return None
        return result["metadatas"][0]

    def increment_post_count(self, profile_url: str) -> None:
        conn_id = self._connection_id(profile_url)
        existing = self.connections.get(ids=[conn_id], include=["documents", "metadatas"])
        if not existing["ids"]:
            return
        meta = dict(existing["metadatas"][0])
        doc = existing["documents"][0]
        meta["post_count"] = meta.get("post_count", 0) + 1
        meta["last_seen"] = datetime.now().date().isoformat()
        self.connections.delete(ids=[conn_id])
        self.connections.add(ids=[conn_id], documents=[doc], metadatas=[meta])
