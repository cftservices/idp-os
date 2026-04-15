from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

# message_store.py is at project root, not in scraper/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import message_store as ms


@pytest.fixture(autouse=True)
def tmp_messages(tmp_path, monkeypatch):
    """Redirect MESSAGES_DIR to a temp directory for all tests."""
    monkeypatch.setattr(ms, "MESSAGES_DIR", tmp_path / "messages")


def test_get_slug_standard_url():
    assert ms.get_slug("https://www.linkedin.com/in/metin-capraz-7b65364a/") == "metin-capraz-7b65364a"


def test_get_slug_no_trailing_slash():
    assert ms.get_slug("https://linkedin.com/in/jeff-winter") == "jeff-winter"


def test_load_conversation_missing_returns_template():
    conv = ms.load_conversation("https://linkedin.com/in/nobody")
    assert conv["messages"] == []
    assert conv["profile_url"] == "https://linkedin.com/in/nobody"


def test_save_and_load_message(tmp_path):
    url = "https://linkedin.com/in/alice"
    msg = {"date": "2026-04-15", "type": "comment", "post_url": "", "post_excerpt": "", "content": "Hello!", "notes": ""}
    ms.save_message(url, "Alice", "Engineer", msg)
    conv = ms.load_conversation(url)
    assert len(conv["messages"]) == 1
    assert conv["messages"][0]["content"] == "Hello!"
    assert conv["name"] == "Alice"


def test_save_message_appends():
    url = "https://linkedin.com/in/alice"
    ms.save_message(url, "Alice", "Engineer", {"date": "2026-04-15", "type": "comment", "post_url": "", "post_excerpt": "", "content": "First", "notes": ""})
    ms.save_message(url, "Alice", "Engineer", {"date": "2026-04-16", "type": "dm", "post_url": "", "post_excerpt": "", "content": "Second", "notes": ""})
    conv = ms.load_conversation(url)
    assert len(conv["messages"]) == 2


def test_save_message_assigns_id():
    url = "https://linkedin.com/in/alice"
    msg = {"date": "2026-04-15", "type": "comment", "post_url": "", "post_excerpt": "", "content": "Hi", "notes": ""}
    ms.save_message(url, "Alice", "Engineer", msg)
    conv = ms.load_conversation(url)
    assert "id" in conv["messages"][0]
    assert len(conv["messages"][0]["id"]) > 0


def test_list_conversations_empty():
    assert ms.list_conversations() == []


def test_list_conversations_sorted_by_date():
    ms.save_message("https://linkedin.com/in/alice", "Alice", "Eng", {"date": "2026-04-10", "type": "dm", "post_url": "", "post_excerpt": "", "content": "Old", "notes": ""})
    ms.save_message("https://linkedin.com/in/bob", "Bob", "Dev", {"date": "2026-04-15", "type": "comment", "post_url": "", "post_excerpt": "", "content": "New", "notes": ""})
    convs = ms.list_conversations()
    assert convs[0]["name"] == "Bob"  # most recent first


def test_delete_message():
    url = "https://linkedin.com/in/alice"
    msg = {"date": "2026-04-15", "type": "comment", "post_url": "", "post_excerpt": "", "content": "Delete me", "notes": ""}
    ms.save_message(url, "Alice", "Eng", msg)
    conv = ms.load_conversation(url)
    msg_id = conv["messages"][0]["id"]
    ms.delete_message(url, msg_id)
    conv = ms.load_conversation(url)
    assert len(conv["messages"]) == 0


def test_delete_message_nonexistent_is_noop():
    url = "https://linkedin.com/in/alice"
    ms.save_message(url, "Alice", "Eng", {"date": "2026-04-15", "type": "dm", "post_url": "", "post_excerpt": "", "content": "Keep", "notes": ""})
    ms.delete_message(url, "nonexistent-id")
    conv = ms.load_conversation(url)
    assert len(conv["messages"]) == 1


def test_build_clipboard_context_contains_name():
    url = "https://linkedin.com/in/alice"
    ms.save_message(url, "Alice", "PLC Engineer", {"date": "2026-04-15", "type": "comment", "post_url": "https://linkedin.com/posts/abc", "post_excerpt": "", "content": "Great post!", "notes": ""})
    ctx = ms.build_clipboard_context(url, [])
    assert "Alice" in ctx
    assert "Great post!" in ctx
    assert "Advies gevraagd" in ctx


def test_build_clipboard_context_includes_chroma_posts():
    url = "https://linkedin.com/in/alice"
    ms.save_message(url, "Alice", "Eng", {"date": "2026-04-15", "type": "dm", "post_url": "", "post_excerpt": "", "content": "Hi", "notes": ""})
    posts = [{"author_profile_url": url, "classification": "ideal_client", "timestamp": "2026-04-14T10:00:00", "text": "OPC UA is great", "url": "https://linkedin.com/posts/xyz"}]
    ctx = ms.build_clipboard_context(url, posts)
    assert "ideal_client" in ctx
    assert "OPC UA is great" in ctx
