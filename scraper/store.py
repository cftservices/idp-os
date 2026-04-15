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
        """Upsert post — update classification/metadata if URL already exists."""
        post_id = self._post_id(post["url"])
        existing = self.posts.get(ids=[post_id], include=["metadatas"])

        metadata = {k: v for k, v in post.items() if k != "text"}
        metadata["keywords_matched"] = json.dumps(metadata.get("keywords_matched", []))
        metadata["reply_drafted"] = bool(metadata.get("reply_drafted", False))
        metadata["engagement_scraped"] = bool(metadata.get("engagement_scraped", False))

        if existing["ids"]:
            # Preserve reply_drafted flag from existing record
            existing_meta = existing["metadatas"][0]
            if existing_meta.get("reply_drafted"):
                metadata["reply_drafted"] = True
            if existing_meta.get("engagement_scraped"):
                metadata["engagement_scraped"] = True
            self.posts.delete(ids=[post_id])

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

    def get_all_posts(self) -> list[dict]:
        """Return all posts stored in DB."""
        results = self.posts.get(include=["documents", "metadatas"])
        posts = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            entry = dict(meta)
            entry["text"] = doc
            entry["keywords_matched"] = json.loads(entry.get("keywords_matched", "[]"))
            posts.append(entry)
        return posts

    def get_all_connections(self) -> list[dict]:
        """Return all connections stored in DB."""
        results = self.connections.get(include=["metadatas"])
        return [dict(meta) for meta in results["metadatas"]]

    def mark_reply_drafted(self, post_url: str, drafted: bool = True) -> None:
        """Set reply_drafted flag on a post atomically. No-op if URL not found."""
        post_id = self._post_id(post_url)
        existing = self.posts.get(ids=[post_id], include=["metadatas"])
        if not existing["ids"]:
            return
        meta = dict(existing["metadatas"][0])
        meta["reply_drafted"] = drafted
        self.posts.update(ids=[post_id], metadatas=[meta])

    def mark_engagement_scraped(self, post_url: str) -> None:
        """Set engagement_scraped=True on a post. No-op if URL not found."""
        post_id = self._post_id(post_url)
        existing = self.posts.get(ids=[post_id], include=["metadatas"])
        if not existing["ids"]:
            return
        meta = dict(existing["metadatas"][0])
        meta["engagement_scraped"] = True
        self.posts.update(ids=[post_id], metadatas=[meta])

    # ── Connections ────────────────────────────────────────────────────────

    def _connection_id(self, profile_url: str) -> str:
        return hashlib.sha256(profile_url.encode()).hexdigest()[:16]

    def upsert_connection(self, connection: dict) -> None:
        """Insert or update a connection. Preserves existing post_count."""
        conn_id = self._connection_id(connection["profile_url"])
        existing = self.connections.get(ids=[conn_id], include=["metadatas"])

        post_count = 0
        about = ""
        if existing["ids"]:
            post_count = existing["metadatas"][0].get("post_count", 0)
            about = existing["metadatas"][0].get("about", "")
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
            "about": connection.get("about", about),
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

    def update_connection_about(self, profile_url: str, about: str) -> None:
        """Atomically store About section text for a connection. No-op if not found."""
        conn_id = self._connection_id(profile_url)
        existing = self.connections.get(ids=[conn_id], include=["metadatas"])
        if not existing["ids"]:
            return
        meta = dict(existing["metadatas"][0])
        meta["about"] = about
        self.connections.update(ids=[conn_id], metadatas=[meta])
