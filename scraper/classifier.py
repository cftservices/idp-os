from __future__ import annotations
import os


def load_config() -> dict:
    """Load keyword lists from environment variables."""
    return {
        "keywords": [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()],
        "influencer_keywords": [k.strip() for k in os.getenv("INFLUENCER_KEYWORDS", "").split(",") if k.strip()],
        "ideal_client_titles": [k.strip() for k in os.getenv("IDEAL_CLIENT_TITLES", "").split(",") if k.strip()],
        "colleague_names": [k.strip() for k in os.getenv("COLLEAGUE_NAMES", "").split(",") if k.strip()],
    }


def classify_post(
    post: dict,
    keywords: list[str],
    influencer_keywords: list[str],
    colleague_names: list[str],
) -> dict:
    """
    Returns the post dict enriched with:
      - classification: ideal_client | influencer | colleague | neutral
      - keywords_matched: list of matched keywords
    """
    text_lower = post.get("text", "").lower()
    author = post.get("author_name", "")

    # Colleague check first (highest priority)
    if any(name.lower() == author.lower() for name in colleague_names if name):
        return {**post, "classification": "colleague", "keywords_matched": []}

    # Keyword matching
    matched = [kw for kw in keywords if kw.lower() in text_lower]
    influencer_matched = [kw for kw in influencer_keywords if kw.lower() in text_lower]

    if influencer_matched:
        return {**post, "classification": "influencer", "keywords_matched": matched + influencer_matched}

    if matched:
        return {**post, "classification": "ideal_client", "keywords_matched": matched}

    return {**post, "classification": "neutral", "keywords_matched": []}


def classify_connection(
    connection: dict,
    ideal_client_titles: list[str],
    colleague_names: list[str],
    influencer_keywords: list[str] | None = None,
) -> str:
    """Returns classification string for a LinkedIn connection.

    Priority: colleague → ideal_client → influencer → unknown
    """
    name = connection.get("name", "")
    title = connection.get("title", "").lower()

    if any(n.lower() == name.lower() for n in colleague_names if n):
        return "colleague"

    if any(t.lower() in title for t in ideal_client_titles if t):
        return "ideal_client"

    if influencer_keywords:
        if any(kw.lower() in title for kw in influencer_keywords if kw):
            return "influencer"

    return "unknown"
