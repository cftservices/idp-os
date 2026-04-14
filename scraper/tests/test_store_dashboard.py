from __future__ import annotations
import json
import pytest
from store import LinkedInStore


@pytest.fixture
def store(tmp_path):
    s = LinkedInStore(str(tmp_path / "chroma"))
    s.add_post({
        "url": "https://linkedin.com/posts/alice-1",
        "text": "OPC UA post by Alice",
        "author_name": "Alice",
        "author_profile_url": "https://linkedin.com/in/alice",
        "timestamp": "2026-04-14T10:00:00",
        "keyword_source": "OPC-UA",
        "classification": "ideal_client",
        "keywords_matched": ["OPC UA"],
        "reply_drafted": False,
    })
    s.add_post({
        "url": "https://linkedin.com/posts/bob-1",
        "text": "MQTT post by Bob",
        "author_name": "Bob",
        "author_profile_url": "https://linkedin.com/in/bob",
        "timestamp": "2026-04-14T11:00:00",
        "keyword_source": "MQTT",
        "classification": "influencer",
        "keywords_matched": ["MQTT"],
        "reply_drafted": False,
    })
    s.upsert_connection({
        "profile_url": "https://linkedin.com/in/alice",
        "name": "Alice",
        "title": "PLC Engineer",
        "company": "Acme",
        "classification": "ideal_client",
        "first_seen": "2026-04-14",
    })
    return s


def test_get_all_posts_returns_all(store):
    posts = store.get_all_posts()
    assert len(posts) == 2
    urls = {p["url"] for p in posts}
    assert "https://linkedin.com/posts/alice-1" in urls
    assert "https://linkedin.com/posts/bob-1" in urls


def test_get_all_posts_includes_text(store):
    posts = store.get_all_posts()
    texts = {p["text"] for p in posts}
    assert "OPC UA post by Alice" in texts


def test_get_all_connections_returns_all(store):
    conns = store.get_all_connections()
    assert len(conns) == 1
    assert conns[0]["name"] == "Alice"


def test_mark_reply_drafted_sets_true(store):
    store.mark_reply_drafted("https://linkedin.com/posts/alice-1", drafted=True)
    posts = store.get_all_posts()
    alice = next(p for p in posts if p["author_name"] == "Alice")
    assert alice["reply_drafted"] is True


def test_mark_reply_drafted_sets_false(store):
    store.mark_reply_drafted("https://linkedin.com/posts/alice-1", drafted=True)
    store.mark_reply_drafted("https://linkedin.com/posts/alice-1", drafted=False)
    posts = store.get_all_posts()
    alice = next(p for p in posts if p["author_name"] == "Alice")
    assert alice["reply_drafted"] is False


def test_mark_reply_drafted_nonexistent_url_is_noop(store):
    store.mark_reply_drafted("https://linkedin.com/posts/nonexistent", drafted=True)
    posts = store.get_all_posts()
    assert len(posts) == 2
