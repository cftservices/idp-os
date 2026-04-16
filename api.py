"""
LinkedIn Intel FastAPI Backend
Bridges ChromaDB (posts + connections) and message_store (JSON files) to the HTML dashboard.
Run: python api.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / "scraper" / ".env")
sys.path.insert(0, str(Path(__file__).parent / "scraper"))
sys.path.insert(0, str(Path(__file__).parent))

from store import LinkedInStore
import message_store as ms

CHROMA_PATH = os.getenv("CHROMA_PATH", "c:/tools/linkedin-intel/db/chroma")

app = FastAPI(title="LinkedIn Intel API", version="1.0.0")

# Allow requests from file:// and localhost origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_store: Optional[LinkedInStore] = None


def get_store() -> LinkedInStore:
    global _store
    if _store is None:
        _store = LinkedInStore(CHROMA_PATH)
    return _store


# ── Pydantic models ────────────────────────────────────────────────────────────

class NewMessage(BaseModel):
    name: str = ""
    title: str = ""
    date: str = ""
    type: str = "comment"
    post_url: str = ""
    post_excerpt: str = ""
    content: str
    notes: str = ""


class ReplyPatch(BaseModel):
    url: str
    drafted: bool = True


class ConnectionPatch(BaseModel):
    classification: str


class ClipboardBody(BaseModel):
    text: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/api/summary")
def summary():
    store = get_store()
    posts = store.get_all_posts()
    connections = store.get_all_connections()
    conversations = ms.list_conversations()
    ideal = sum(1 for p in posts if p.get("classification") == "ideal_client")
    influencer = sum(1 for p in posts if p.get("classification") == "influencer")
    replied = sum(1 for p in posts if p.get("reply_drafted"))
    return {
        "total_posts": len(posts),
        "ideal_clients": ideal,
        "influencers": influencer,
        "replied": replied,
        "connections": len(connections),
        "conversations": len(conversations),
    }


@app.get("/api/posts")
def get_posts(classification: str = "", search: str = ""):
    store = get_store()
    posts = store.get_all_posts()
    # Sort newest first
    posts.sort(key=lambda p: p.get("timestamp", ""), reverse=True)
    if classification:
        posts = [p for p in posts if p.get("classification") == classification]
    if search:
        q = search.lower()
        posts = [p for p in posts if q in (p.get("text", "") + p.get("author_name", "")).lower()]
    return posts


@app.get("/api/connections")
def get_connections():
    store = get_store()
    connections = store.get_all_connections()
    conversations = ms.list_conversations()
    msg_map = {c.get("profile_url", ""): c for c in conversations}

    # Attach message count and last message date to ChromaDB connections
    conn_urls = {c.get("profile_url", "") for c in connections}
    for conn in connections:
        url = conn.get("profile_url", "")
        conv = msg_map.get(url)
        conn["message_count"] = len(conv.get("messages", [])) if conv else 0
        conn["last_message"] = conv.get("_last_date", "") if conv else ""

    # Add contacts that exist only in message store (e.g. DM contacts not yet in ChromaDB)
    for conv in conversations:
        url = conv.get("profile_url", "")
        if url and url not in conn_urls:
            connections.append({
                "profile_url": url,
                "name": conv.get("name", "?"),
                "title": conv.get("title", ""),
                "company": "",
                "classification": "unknown",
                "first_seen": "",
                "last_seen": "",
                "post_count": 0,
                "about": "",
                "message_count": len(conv.get("messages", [])),
                "last_message": conv.get("_last_date", ""),
            })

    # Sort: ideal_client+msgs first, then ideal_client, influencer+msgs, influencer, rest
    # Within each group: most recent message first
    cls_rank = {"ideal_client": 0, "influencer": 2, "colleague": 4, "unknown": 4}

    def sort_key(c):
        rank = cls_rank.get(c.get("classification", "unknown"), 4)
        has_msgs = 0 if c.get("message_count", 0) > 0 else 1
        # Negate last_message for descending date within group
        last = c.get("last_message", "")
        neg = "".join(chr(127 - ord(ch)) for ch in last) if last else "~"
        return (rank + has_msgs, neg)

    connections.sort(key=sort_key)
    return connections


@app.get("/api/conversations")
def get_conversations():
    return ms.list_conversations()


@app.get("/api/conversations/{slug}")
def get_conversation(slug: str):
    # Find profile_url by slug
    convs = ms.list_conversations()
    for conv in convs:
        url = conv.get("profile_url", "")
        if ms.get_slug(url) == slug:
            return ms.load_conversation(url)
    raise HTTPException(status_code=404, detail="Conversation not found")


@app.post("/api/conversations/{slug}/messages")
def add_message(slug: str, body: NewMessage):
    # Resolve profile_url
    profile_url = _resolve_url_from_slug(slug)
    if not profile_url:
        raise HTTPException(status_code=404, detail="Contact not found")
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    date = body.date or datetime.now().date().isoformat()
    msg = {
        "date": date,
        "type": body.type,
        "post_url": body.post_url.strip(),
        "post_excerpt": body.post_excerpt.strip()[:150],
        "content": body.content.strip(),
        "notes": body.notes.strip(),
    }
    ms.save_message(profile_url, body.name, body.title, msg)
    return {"ok": True}


@app.delete("/api/conversations/{slug}/messages/{message_id}")
def delete_message(slug: str, message_id: str):
    profile_url = _resolve_url_from_slug(slug)
    if not profile_url:
        raise HTTPException(status_code=404, detail="Contact not found")
    ms.delete_message(profile_url, message_id)
    return {"ok": True}


@app.patch("/api/posts/reply")
def mark_reply(body: ReplyPatch):
    store = get_store()
    store.mark_reply_drafted(body.url, body.drafted)
    return {"ok": True}


@app.patch("/api/connections/{slug}/classification")
def update_classification(slug: str, body: ConnectionPatch):
    store = get_store()
    connections = store.get_all_connections()
    conn = next((c for c in connections if ms.get_slug(c.get("profile_url", "")) == slug), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    conn["classification"] = body.classification
    store.upsert_connection(conn)
    return {"ok": True}


@app.get("/api/context/{slug}")
def get_context(slug: str):
    profile_url = _resolve_url_from_slug(slug)
    if not profile_url:
        raise HTTPException(status_code=404, detail="Contact not found")
    store = get_store()
    all_posts = store.get_all_posts()
    chroma_conn = store.get_connection(profile_url)
    if chroma_conn and chroma_conn.get("about"):
        for p in all_posts:
            if p.get("author_profile_url") == profile_url:
                p["about"] = chroma_conn["about"]
    ctx = ms.build_clipboard_context(profile_url, all_posts)
    return {"context": ctx}


@app.get("/api/posts/{post_id}/context")
def get_post_context(post_id: str):
    """Get context for a specific post (for /linkedin-reply)."""
    store = get_store()
    all_posts = store.get_all_posts()
    # post_id is URL-encoded post URL — decode it
    from urllib.parse import unquote
    post_url = unquote(post_id)
    post = next((p for p in all_posts if p.get("url") == post_url), None)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    author_url = post.get("author_profile_url", "")
    conn = store.get_connection(author_url) or {}
    conv = ms.load_conversation(author_url)
    return {
        "post": post,
        "author_connection": conn,
        "author_messages": conv.get("messages", []),
    }


@app.post("/api/clipboard")
def set_clipboard(body: ClipboardBody):
    """Copy text to Windows clipboard via PowerShell (works from background service)."""
    import tempfile, os
    tmp = None
    try:
        # Write to temp file, then use PowerShell to read + Set-Clipboard
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         suffix=".txt", delete=False) as f:
            f.write(body.text)
            tmp = f.name
        result = subprocess.run(
            ["powershell", "-noprofile", "-noninteractive",
             "-command", f'Get-Content -Raw -Path "{tmp}" | Set-Clipboard'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=result.stderr.strip())
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp:
            try: os.unlink(tmp)
            except Exception: pass


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_url_from_slug(slug: str) -> str:
    """Find profile_url matching slug from connections or message store."""
    store = get_store()
    # Check connections first
    connections = store.get_all_connections()
    for conn in connections:
        url = conn.get("profile_url", "")
        if ms.get_slug(url) == slug:
            return url
    # Fallback: check message store
    convs = ms.list_conversations()
    for conv in convs:
        url = conv.get("profile_url", "")
        if ms.get_slug(url) == slug:
            return url
    return ""


if __name__ == "__main__":
    print("LinkedIn Intel API — http://localhost:8000")
    print("Docs — http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
