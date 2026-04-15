from __future__ import annotations
import pytest
from store import LinkedInStore


@pytest.fixture
def store(tmp_path):
    s = LinkedInStore(str(tmp_path / "chroma"))
    s.upsert_connection({
        "profile_url": "https://linkedin.com/in/alice",
        "name": "Alice",
        "title": "PLC Engineer",
        "company": "Acme",
        "classification": "ideal_client",
        "first_seen": "2026-04-15",
    })
    s.add_post({
        "url": "https://linkedin.com/posts/alice-1",
        "text": "OPC UA is great",
        "author_name": "Alice",
        "author_profile_url": "https://linkedin.com/in/alice",
        "timestamp": "2026-04-15T10:00:00",
        "keyword_source": "OPC-UA",
        "classification": "ideal_client",
        "keywords_matched": ["OPC UA"],
        "reply_drafted": False,
    })
    return s


def test_update_connection_about_stores_text(store):
    store.update_connection_about("https://linkedin.com/in/alice", "I build OT systems.")
    conn = store.get_connection("https://linkedin.com/in/alice")
    assert conn["about"] == "I build OT systems."


def test_update_connection_about_nonexistent_is_noop(store):
    # Should not raise
    store.update_connection_about("https://linkedin.com/in/nobody", "text")


def test_upsert_connection_preserves_about_on_update(store):
    store.update_connection_about("https://linkedin.com/in/alice", "My About text")
    # Re-upsert without about field
    store.upsert_connection({
        "profile_url": "https://linkedin.com/in/alice",
        "name": "Alice",
        "title": "Senior PLC Engineer",
        "company": "Acme",
        "classification": "ideal_client",
        "first_seen": "2026-04-15",
    })
    conn = store.get_connection("https://linkedin.com/in/alice")
    assert conn["about"] == "My About text"


def test_add_post_default_engagement_scraped_false(store):
    posts = store.get_all_posts()
    alice = next(p for p in posts if p["url"] == "https://linkedin.com/posts/alice-1")
    assert alice.get("engagement_scraped") is False


def test_add_post_preserves_engagement_scraped_on_upsert(store):
    # Set engagement_scraped=True via direct update
    post_id = store._post_id("https://linkedin.com/posts/alice-1")
    existing = store.posts.get(ids=[post_id], include=["metadatas"])
    meta = dict(existing["metadatas"][0])
    meta["engagement_scraped"] = True
    store.posts.update(ids=[post_id], metadatas=[meta])

    # Re-upsert via add_post — engagement_scraped must stay True
    store.add_post({
        "url": "https://linkedin.com/posts/alice-1",
        "text": "Updated OPC UA post",
        "author_name": "Alice",
        "author_profile_url": "https://linkedin.com/in/alice",
        "timestamp": "2026-04-15T11:00:00",
        "keyword_source": "OPC-UA",
        "classification": "ideal_client",
        "keywords_matched": ["OPC UA"],
        "reply_drafted": False,
    })
    posts = store.get_all_posts()
    alice = next(p for p in posts if p["url"] == "https://linkedin.com/posts/alice-1")
    assert alice.get("engagement_scraped") is True


def test_mark_engagement_scraped(store):
    store.mark_engagement_scraped("https://linkedin.com/posts/alice-1")
    posts = store.get_all_posts()
    alice = next(p for p in posts if p["url"] == "https://linkedin.com/posts/alice-1")
    assert alice.get("engagement_scraped") is True


def test_mark_engagement_scraped_nonexistent_is_noop(store):
    store.mark_engagement_scraped("https://linkedin.com/posts/nobody")
    # Just verify no exception raised and post count unchanged
    assert len(store.get_all_posts()) == 1
