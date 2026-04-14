from report_generator import generate_report

SAMPLE_POSTS = [
    {
        "url": "https://linkedin.com/feed/update/111",
        "text": "Still exporting SCADA data to Excel every morning...",
        "author_name": "Jan de Vries",
        "author_profile_url": "https://linkedin.com/in/jandevries",
        "timestamp": "2026-04-14T08:00:00",
        "classification": "ideal_client",
        "keywords_matched": ["SCADA"],
        "reply_drafted": False,
    },
    {
        "url": "https://linkedin.com/feed/update/222",
        "text": "UNS is not a product, it is a philosophy",
        "author_name": "Walker Reynolds",
        "author_profile_url": "https://linkedin.com/in/walkerreynolds",
        "timestamp": "2026-04-14T07:00:00",
        "classification": "influencer",
        "keywords_matched": ["UNS"],
        "reply_drafted": False,
    },
]

SAMPLE_CONNECTIONS = {
    "https://linkedin.com/in/jandevries": {
        "name": "Jan de Vries",
        "title": "PLC Engineer",
        "company": "TechFlow BV",
        "classification": "ideal_client",
        "first_seen": "2026-03-01",
        "post_count": 3,
        "last_seen": "2026-04-14",
    },
}

SAMPLE_REPLIES = {
    "https://linkedin.com/feed/update/111": [
        "Herken dit volledig. Gebruik je al een MQTT broker als tussenlaag?",
        "Welke SCADA gebruik je? Sommige hebben een verborgen REST API.",
    ]
}


def test_report_contains_date():
    report = generate_report("2026-04-14", SAMPLE_POSTS, SAMPLE_CONNECTIONS, SAMPLE_REPLIES)
    assert "2026-04-14" in report


def test_report_contains_ideal_client_section():
    report = generate_report("2026-04-14", SAMPLE_POSTS, SAMPLE_CONNECTIONS, SAMPLE_REPLIES)
    assert "PRIORITEIT 1" in report
    assert "Jan de Vries" in report


def test_report_contains_influencer_section():
    report = generate_report("2026-04-14", SAMPLE_POSTS, SAMPLE_CONNECTIONS, SAMPLE_REPLIES)
    assert "PRIORITEIT 2" in report
    assert "Walker Reynolds" in report


def test_report_contains_post_url():
    report = generate_report("2026-04-14", SAMPLE_POSTS, SAMPLE_CONNECTIONS, SAMPLE_REPLIES)
    assert "linkedin.com/feed/update/111" in report


def test_report_contains_reply_options():
    report = generate_report("2026-04-14", SAMPLE_POSTS, SAMPLE_CONNECTIONS, SAMPLE_REPLIES)
    assert "MQTT broker" in report


def test_report_summary_counts():
    report = generate_report("2026-04-14", SAMPLE_POSTS, SAMPLE_CONNECTIONS, SAMPLE_REPLIES)
    assert "ideal client" in report.lower()
    assert "influencer" in report.lower()


def test_report_cumulative_leads_table():
    report = generate_report("2026-04-14", SAMPLE_POSTS, SAMPLE_CONNECTIONS, SAMPLE_REPLIES)
    assert "OVERZICHT LEADS" in report
    assert "TechFlow BV" in report
