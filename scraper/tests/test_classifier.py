import pytest
from classifier import classify_post, classify_connection, load_config

KEYWORDS = ["MQTT", "OPC-UA", "SCADA", "PLC", "IIoT"]
INFLUENCER_KEYWORDS = ["Industry 4.0", "UNS", "Unified Namespace"]
IDEAL_CLIENT_TITLES = ["PLC", "SCADA", "automation engineer", "system integrator"]
COLLEAGUE_NAMES = ["Jan Collega", "Chef Naam"]


def test_classify_post_ideal_client_by_keyword():
    post = {"text": "Trying to get MQTT data into our historian...", "author_name": "Unknown"}
    result = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
    assert result["classification"] == "ideal_client"
    assert "MQTT" in result["keywords_matched"]


def test_classify_post_influencer():
    post = {"text": "UNS is not a product, it is a philosophy for Industry 4.0", "author_name": "Walker"}
    result = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
    assert result["classification"] == "influencer"


def test_classify_post_colleague():
    post = {"text": "Great day at the office!", "author_name": "Jan Collega"}
    result = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
    assert result["classification"] == "colleague"


def test_classify_post_neutral():
    post = {"text": "Just had a great coffee", "author_name": "Random Person"}
    result = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
    assert result["classification"] == "neutral"
    assert result["keywords_matched"] == []


def test_classify_connection_ideal_client():
    connection = {"name": "Pieter Smit", "title": "PLC Engineer", "company": "ABB NL"}
    result = classify_connection(connection, IDEAL_CLIENT_TITLES, COLLEAGUE_NAMES)
    assert result == "ideal_client"


def test_classify_connection_colleague():
    connection = {"name": "Chef Naam", "title": "Manager", "company": "Acme"}
    result = classify_connection(connection, IDEAL_CLIENT_TITLES, COLLEAGUE_NAMES)
    assert result == "colleague"


def test_classify_connection_unknown():
    connection = {"name": "Random Human", "title": "Marketing Director", "company": "Brand Co"}
    result = classify_connection(connection, IDEAL_CLIENT_TITLES, COLLEAGUE_NAMES)
    assert result == "unknown"


def test_case_insensitive_keyword_match():
    post = {"text": "Working with mqtt broker today", "author_name": "Someone"}
    result = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
    assert result["classification"] == "ideal_client"
