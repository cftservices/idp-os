from __future__ import annotations
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
import chromadb


class LinkedInStore:
    """Local ChromaDB store for LinkedIn posts and connections."""

    def __init__(self, chroma_path: str):
        self.client = chromadb.PersistentClient(path=chroma_path)

        # Use chromadb default embedding (no external deps needed)
        self.posts = self.client.get_or_create_collection(name="posts")
        self.connections = self.client.get_or_create_collection(name="connections")
        self.messages = self.client.get_or_create_collection(name="messages")

    # ── Messages ────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Strip query params and trailing slash for consistent dedup keys."""
        return url.split("?")[0].rstrip("/") if url else url

    def _msg_chroma_id(self, message_id: str) -> str:
        return hashlib.sha256(message_id.encode()).hexdigest()[:16]

    def save_message(self, profile_url: str, name: str, title: str, message: dict) -> None:
        """Upsert a single message into ChromaDB."""
        profile_url = self._normalize_url(profile_url)
        msg_id = message.get("id") or str(uuid.uuid4())
        chroma_id = self._msg_chroma_id(msg_id)
        content = message.get("content", "").strip()
        if not content:
            return
        metadata = {
            "profile_url": profile_url,
            "name": name or "",
            "title": title or "",
            "date": message.get("date", ""),
            "type": message.get("type", "dm"),
            "direction": message.get("direction", "sent"),
            "post_url": message.get("post_url", "") or "",
            "msg_id": msg_id,
        }
        existing = self.messages.get(ids=[chroma_id])
        if existing["ids"]:
            return  # already stored
        self.messages.add(ids=[chroma_id], documents=[content], metadatas=[metadata])

    def save_scraped_messages(
        self, profile_url: str, name: str, title: str, messages: list[dict]
    ) -> int:
        """Batch upsert messages with dedup. Returns number of newly saved messages."""
        profile_url = self._normalize_url(profile_url)
        existing_results = self.messages.get(
            where={"profile_url": profile_url},
            include=["metadatas"],
        )
        existing_keys = {
            meta.get("msg_id", "")
            for meta in (existing_results.get("metadatas") or [])
        }
        new_count = 0
        for i, message in enumerate(messages):
            content = message.get("content", "").strip()
            if not content:
                continue
            direction = message.get("direction", "sent")
            if message.get("id"):
                msg_id = message["id"]
            else:
                # Stable dedup key: normalize whitespace so minor DOM variations don't break dedup
                content_norm = re.sub(r'\s+', ' ', content)
                key = f"{profile_url}|{message.get('date','')}|{direction}|{content_norm[:80]}"
                msg_id = hashlib.sha256(key.encode()).hexdigest()[:24]
            if msg_id in existing_keys:
                continue
            chroma_id = self._msg_chroma_id(msg_id)
            # Belt-and-suspenders: check chroma_id directly in case profile_url changed
            if self.messages.get(ids=[chroma_id])["ids"]:
                existing_keys.add(msg_id)
                continue
            metadata = {
                "profile_url": profile_url,
                "name": name or "",
                "title": title or "",
                "date": message.get("date", ""),
                "type": message.get("type", "dm"),
                "direction": direction,
                "post_url": message.get("post_url", "") or "",
                "msg_id": msg_id,
            }
            self.messages.add(ids=[chroma_id], documents=[content], metadatas=[metadata])
            existing_keys.add(msg_id)
            new_count += 1
        return new_count

    def load_conversation(self, profile_url: str) -> dict:
        """Return all messages for a profile as {profile_url, name, title, messages}."""
        profile_url = self._normalize_url(profile_url)
        results = self.messages.get(
            where={"profile_url": profile_url},
            include=["documents", "metadatas"],
        )
        if not results["ids"]:
            return {"profile_url": profile_url, "name": "", "title": "", "messages": []}
        messages = []
        name = ""
        title = ""
        for doc, meta in zip(results["documents"], results["metadatas"]):
            if not name:
                name = meta.get("name", "") or ""
            if not title:
                title = meta.get("title", "") or ""
            messages.append({
                "id": meta.get("msg_id", ""),
                "date": meta.get("date", ""),
                "type": meta.get("type", "dm"),
                "direction": meta.get("direction", "sent"),
                "content": doc,
                "post_url": meta.get("post_url", ""),
            })
        messages.sort(key=lambda m: m.get("date", ""))
        return {"profile_url": profile_url, "name": name, "title": title, "messages": messages}

    def list_conversations(self) -> list[dict]:
        """All conversations sorted by most recent message date (newest first)."""
        results = self.messages.get(include=["documents", "metadatas"])
        if not results["ids"]:
            return []
        profiles: dict[str, dict] = {}
        for doc, meta in zip(results["documents"], results["metadatas"]):
            url = meta.get("profile_url", "")
            if url not in profiles:
                profiles[url] = {
                    "profile_url": url,
                    "name": meta.get("name", ""),
                    "title": meta.get("title", ""),
                    "messages": [],
                    "_last_date": "",
                }
            profiles[url]["messages"].append({
                "id": meta.get("msg_id", ""),
                "date": meta.get("date", ""),
                "type": meta.get("type", "dm"),
                "direction": meta.get("direction", "sent"),
                "content": doc,
                "post_url": meta.get("post_url", ""),
            })
            d = meta.get("date", "")
            if d > profiles[url]["_last_date"]:
                profiles[url]["_last_date"] = d
        convs = list(profiles.values())
        convs.sort(key=lambda c: c["_last_date"], reverse=True)
        for c in convs:
            c["messages"].sort(key=lambda m: m.get("date", ""))
            c.pop("_last_date", None)
        return convs

    def delete_message(self, profile_url: str, message_id: str) -> None:
        """Delete a message by its msg_id. No-op if not found or profile_url mismatch."""
        chroma_id = self._msg_chroma_id(message_id)
        existing = self.messages.get(ids=[chroma_id], include=["metadatas"])
        if not existing["ids"]:
            return
        if existing["metadatas"][0].get("profile_url") != profile_url:
            return
        self.messages.delete(ids=[chroma_id])

    def search_messages(
        self, query: str, n_results: int = 10, profile_url: str | None = None
    ) -> list[dict]:
        """Semantic search across messages. Optionally filter by profile_url."""
        if not query or not query.strip():
            return []
        where = {"profile_url": profile_url} if profile_url else None
        total = self.messages.count()
        if total == 0:
            return []
        actual_n = min(n_results, total)
        kwargs: dict = {"query_texts": [query], "n_results": actual_n, "include": ["documents", "metadatas"]}
        if where:
            kwargs["where"] = where
        results = self.messages.query(**kwargs)
        output = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            output.append({
                "profile_url": meta.get("profile_url", ""),
                "name": meta.get("name", ""),
                "date": meta.get("date", ""),
                "direction": meta.get("direction", ""),
                "content": doc,
            })
        return output

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
