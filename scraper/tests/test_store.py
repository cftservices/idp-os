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
