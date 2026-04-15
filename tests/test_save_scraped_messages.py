import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import message_store as ms


@pytest.fixture(autouse=True)
def tmp_messages_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "MESSAGES_DIR", tmp_path)


def test_saves_new_messages():
    msgs = [
        {"date": "2026-04-15", "type": "dm", "content": "Hello there"},
        {"date": "2026-04-15", "type": "dm", "content": "Following up"},
    ]
    saved = ms.save_scraped_messages(
        "https://www.linkedin.com/in/test-user/", "Test User", "Engineer", msgs
    )
    assert saved == 2
    conv = ms.load_conversation("https://www.linkedin.com/in/test-user/")
    assert len(conv["messages"]) == 2


def test_deduplicates_on_second_run():
    msgs = [{"date": "2026-04-15", "type": "dm", "content": "Hello there"}]
    ms.save_scraped_messages("https://www.linkedin.com/in/test-user/", "Test", "", msgs)
    saved = ms.save_scraped_messages("https://www.linkedin.com/in/test-user/", "Test", "", msgs)
    assert saved == 0
    conv = ms.load_conversation("https://www.linkedin.com/in/test-user/")
    assert len(conv["messages"]) == 1


def test_new_message_in_second_run_is_saved():
    msgs1 = [{"date": "2026-04-14", "type": "dm", "content": "First message"}]
    msgs2 = [
        {"date": "2026-04-14", "type": "dm", "content": "First message"},
        {"date": "2026-04-15", "type": "dm", "content": "Second message"},
    ]
    ms.save_scraped_messages("https://www.linkedin.com/in/test-user/", "Test", "", msgs1)
    saved = ms.save_scraped_messages("https://www.linkedin.com/in/test-user/", "Test", "", msgs2)
    assert saved == 1
    conv = ms.load_conversation("https://www.linkedin.com/in/test-user/")
    assert len(conv["messages"]) == 2


def test_skips_empty_content():
    msgs = [{"date": "2026-04-15", "type": "dm", "content": ""}]
    saved = ms.save_scraped_messages("https://www.linkedin.com/in/test-user/", "Test", "", msgs)
    assert saved == 0
