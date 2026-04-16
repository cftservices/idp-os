import pytest
import tempfile
from store import LinkedInStore


@pytest.fixture
def tmp_store(tmp_path):
    return LinkedInStore(chroma_path=str(tmp_path))


def test_add_and_retrieve_post(tmp_store):
    post = {
        "url": "https://linkedin.com/feed/update/123",
        "text": "MQTT is changing the way we connect PLCs",
        "author_profile_url": "https://linkedin.com/in/jandevries",
        "author_name": "Jan de Vries",
        "timestamp": "2026-04-14T09:00:00",
        "classification": "ideal_client",
        "keywords_matched": ["MQTT", "PLC"],
        "reply_drafted": False,
    }
    tmp_store.add_post(post)
    results = tmp_store.get_recent_posts(hours=48)
    assert len(results) == 1
    assert results[0]["url"] == post["url"]


def test_duplicate_post_not_added(tmp_store):
    post = {
        "url": "https://linkedin.com/feed/update/123",
        "text": "MQTT post",
        "author_profile_url": "https://linkedin.com/in/jan",
        "author_name": "Jan",
        "timestamp": "2026-04-14T09:00:00",
        "classification": "ideal_client",
        "keywords_matched": ["MQTT"],
        "reply_drafted": False,
    }
    tmp_store.add_post(post)
    tmp_store.add_post(post)  # duplicate
    results = tmp_store.get_recent_posts(hours=48)
    assert len(results) == 1


def test_add_and_retrieve_connection(tmp_store):
    connection = {
        "profile_url": "https://linkedin.com/in/jandevries",
        "name": "Jan de Vries",
        "title": "PLC Engineer",
        "company": "TechFlow BV",
        "classification": "ideal_client",
    }
    tmp_store.upsert_connection(connection)
    result = tmp_store.get_connection("https://linkedin.com/in/jandevries")
    assert result is not None
    assert result["name"] == "Jan de Vries"
    assert result["post_count"] == 0


def test_increment_post_count(tmp_store):
    connection = {
        "profile_url": "https://linkedin.com/in/jan",
        "name": "Jan",
        "title": "SCADA Engineer",
        "company": "ABB",
        "classification": "ideal_client",
    }
    tmp_store.upsert_connection(connection)
    tmp_store.increment_post_count("https://linkedin.com/in/jan")
    tmp_store.increment_post_count("https://linkedin.com/in/jan")
    result = tmp_store.get_connection("https://linkedin.com/in/jan")
    assert result["post_count"] == 2


def test_get_daily_summary(tmp_store):
    from datetime import datetime
    today = datetime.now().date().isoformat()
    for i, cls in enumerate(["ideal_client", "ideal_client", "influencer", "neutral"]):
        tmp_store.add_post({
            "url": f"https://linkedin.com/feed/update/{i}",
            "text": f"Post {i}",
            "author_profile_url": f"https://linkedin.com/in/person{i}",
            "author_name": f"Person {i}",
            "timestamp": f"{today}T09:00:00",
            "classification": cls,
            "keywords_matched": [],
            "reply_drafted": False,
        })
    summary = tmp_store.get_daily_summary()
    assert summary["ideal_client"] == 2
    assert summary["influencer"] == 1
    assert summary["neutral"] == 1


# ── Message tests ──────────────────────────────────────────────────────────

def test_save_and_load_message(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    msg = {
        "id": "msg-001",
        "date": "2026-04-16",
        "type": "dm",
        "direction": "received",
        "content": "Hi Johannes!",
        "post_url": "",
    }
    store.save_message("https://linkedin.com/in/jorge", "Jorge", "Engineer", msg)
    conv = store.load_conversation("https://linkedin.com/in/jorge")
    assert conv["name"] == "Jorge"
    assert len(conv["messages"]) == 1
    assert conv["messages"][0]["content"] == "Hi Johannes!"


def test_save_message_dedup(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    msg = {"id": "msg-001", "date": "2026-04-16", "type": "dm", "direction": "sent", "content": "Hello", "post_url": ""}
    store.save_message("https://linkedin.com/in/jorge", "Jorge", "", msg)
    store.save_message("https://linkedin.com/in/jorge", "Jorge", "", msg)  # duplicate
    conv = store.load_conversation("https://linkedin.com/in/jorge")
    assert len(conv["messages"]) == 1


def test_save_scraped_messages_returns_count(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    msgs = [
        {"id": "a", "date": "2026-04-16", "type": "dm", "direction": "sent", "content": "First", "post_url": ""},
        {"id": "b", "date": "2026-04-16", "type": "dm", "direction": "received", "content": "Second", "post_url": ""},
    ]
    count = store.save_scraped_messages("https://linkedin.com/in/jorge", "Jorge", "", msgs)
    assert count == 2


def test_save_scraped_messages_dedup(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    msgs = [{"id": "a", "date": "2026-04-16", "type": "dm", "direction": "sent", "content": "Hello", "post_url": ""}]
    store.save_scraped_messages("https://linkedin.com/in/jorge", "Jorge", "", msgs)
    count = store.save_scraped_messages("https://linkedin.com/in/jorge", "Jorge", "", msgs)
    assert count == 0  # already exists


def test_delete_message(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    msg = {"id": "msg-del", "date": "2026-04-16", "type": "dm", "direction": "sent", "content": "Delete me", "post_url": ""}
    store.save_message("https://linkedin.com/in/jorge", "Jorge", "", msg)
    store.delete_message("https://linkedin.com/in/jorge", "msg-del")
    conv = store.load_conversation("https://linkedin.com/in/jorge")
    assert len(conv["messages"]) == 0


def test_delete_message_noop(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    store.delete_message("https://linkedin.com/in/nobody", "nonexistent-id")


def test_list_conversations_sorted(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    store.save_message("https://linkedin.com/in/alice", "Alice", "", {"id": "a1", "date": "2026-04-10", "type": "dm", "direction": "sent", "content": "Old", "post_url": ""})
    store.save_message("https://linkedin.com/in/bob", "Bob", "", {"id": "b1", "date": "2026-04-15", "type": "dm", "direction": "sent", "content": "New", "post_url": ""})
    convs = store.list_conversations()
    assert convs[0]["name"] == "Bob"  # most recent first


def test_search_messages_across_conversations(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    store.save_message("https://linkedin.com/in/jorge", "Jorge", "", {"id": "j1", "date": "2026-04-16", "type": "dm", "direction": "received", "content": "I work with B&R PLCs and machine automation", "post_url": ""})
    store.save_message("https://linkedin.com/in/alice", "Alice", "", {"id": "a1", "date": "2026-04-16", "type": "dm", "direction": "sent", "content": "Let me know about your project", "post_url": ""})
    results = store.search_messages("B&R machine automation", n_results=5)
    assert len(results) >= 1
    assert any("B&R" in r["content"] for r in results)


def test_search_messages_filtered_by_profile(tmp_path):
    store = LinkedInStore(str(tmp_path / "chroma"))
    store.save_message("https://linkedin.com/in/jorge", "Jorge", "", {"id": "j1", "date": "2026-04-16", "type": "dm", "direction": "received", "content": "machine automation project", "post_url": ""})
    store.save_message("https://linkedin.com/in/alice", "Alice", "", {"id": "a1", "date": "2026-04-16", "type": "dm", "direction": "sent", "content": "machine automation project", "post_url": ""})
    results = store.search_messages("machine automation", n_results=5, profile_url="https://linkedin.com/in/jorge")
    assert all(r["profile_url"] == "https://linkedin.com/in/jorge" for r in results)
