from __future__ import annotations
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page

load_dotenv(Path(__file__).parent / ".env")

CHROMIUM_PROFILE = os.getenv("CHROMIUM_PROFILE", "c:/tools/linkedin-intel/browser-profile")
MAX_SCROLL_HOURS = int(os.getenv("MAX_SCROLL_HOURS", "24"))
MAX_POSTS = int(os.getenv("MAX_POSTS", "150"))


def setup_browser(playwright):
    """Launch Chromium with persistent profile (stays logged in)."""
    return playwright.chromium.launch_persistent_context(
        user_data_dir=CHROMIUM_PROFILE,
        headless=False,
        args=["--start-maximized"],
        no_viewport=True,
    )


def parse_relative_time(time_text: str) -> str | None:
    """
    Convert LinkedIn relative timestamps to ISO8601.
    Returns None if older than MAX_SCROLL_HOURS (signals stop scrolling).
    """
    now = datetime.now()
    text = time_text.lower().strip()

    if not text or "just now" in text or "nu" in text or "zojuist" in text:
        return now.isoformat()

    match = re.search(r"(\d+)\s*(s|m|h|d|w|min|sec|hour|uur|dag|week|minuut|uur)", text)
    if not match:
        return now.isoformat()

    value, unit = int(match.group(1)), match.group(2)
    if unit in ("s", "sec"):
        delta = timedelta(seconds=value)
    elif unit in ("m", "min", "minuut"):
        delta = timedelta(minutes=value)
    elif unit in ("h", "hour", "uur"):
        delta = timedelta(hours=value)
    elif unit in ("d", "dag"):
        delta = timedelta(days=value)
    elif unit in ("w", "week"):
        delta = timedelta(weeks=value)
    else:
        delta = timedelta(hours=value)

    if delta.total_seconds() > MAX_SCROLL_HOURS * 3600:
        return None
    return (now - delta).isoformat()


def scrape_feed(page: Page) -> list[dict]:
    """Scroll LinkedIn feed and extract posts from last 24h."""
    page.goto("https://www.linkedin.com/feed/", wait_until="networkidle")
    time.sleep(3)

    posts = []
    seen_urls: set[str] = set()
    cutoff_reached = False
    scroll_count = 0
    max_scrolls = 80

    while not cutoff_reached and scroll_count < max_scrolls and len(posts) < MAX_POSTS:
        post_elements = page.query_selector_all(
            "div.feed-shared-update-v2, div[data-urn*='activity']"
        )

        for el in post_elements:
            try:
                # Post URL from timestamp link
                time_el = el.query_selector("a[href*='activity']")
                if not time_el:
                    continue
                post_url = time_el.get_attribute("href") or ""
                if not post_url.startswith("http"):
                    post_url = "https://www.linkedin.com" + post_url
                post_url = post_url.split("?")[0]

                if post_url in seen_urls:
                    continue

                time_text = time_el.inner_text().strip()
                timestamp = parse_relative_time(time_text)

                if timestamp is None:
                    cutoff_reached = True
                    break

                # Post text
                text_el = el.query_selector(
                    "span.break-words, div.feed-shared-text, div.update-components-text"
                )
                text = text_el.inner_text().strip() if text_el else ""
                if not text or len(text) < 20:
                    continue

                # Author
                author_el = el.query_selector(
                    "span.feed-shared-actor__name, a.app-aware-link span[aria-hidden='true']"
                )
                author_name = author_el.inner_text().strip() if author_el else "Unknown"

                author_link_el = el.query_selector("a.app-aware-link[href*='/in/']")
                author_profile_url = ""
                if author_link_el:
                    href = author_link_el.get_attribute("href") or ""
                    author_profile_url = href.split("?")[0]
                    if author_profile_url.startswith("/"):
                        author_profile_url = "https://www.linkedin.com" + author_profile_url

                seen_urls.add(post_url)
                posts.append({
                    "url": post_url,
                    "text": text,
                    "author_name": author_name,
                    "author_profile_url": author_profile_url,
                    "timestamp": timestamp,
                })

            except Exception as e:
                print(f"[scraper] post parse error: {e}", file=sys.stderr)
                continue

        if not cutoff_reached:
            page.evaluate("window.scrollBy(0, 1200)")
            time.sleep(2.5)
            scroll_count += 1

    print(f"[scraper] feed: {len(posts)} posts collected ({scroll_count} scrolls)", file=sys.stderr)
    return posts


def scrape_connections(page: Page) -> list[dict]:
    """Scrape first-degree connections."""
    page.goto(
        "https://www.linkedin.com/mynetwork/invite-connect/connections/",
        wait_until="networkidle",
    )
    time.sleep(3)

    connections = []
    seen_urls: set[str] = set()
    scroll_count = 0
    max_scrolls = 30

    while scroll_count < max_scrolls:
        cards = page.query_selector_all(
            "li.mn-connection-card, div.scaffold-finite-scroll__content li"
        )

        for card in cards:
            try:
                link_el = card.query_selector("a[href*='/in/']")
                if not link_el:
                    continue
                profile_url = (link_el.get_attribute("href") or "").split("?")[0]
                if not profile_url or profile_url in seen_urls:
                    continue
                if profile_url.startswith("/"):
                    profile_url = "https://www.linkedin.com" + profile_url

                name_el = card.query_selector(
                    "span.mn-connection-card__name, span.t-16"
                )
                name = name_el.inner_text().strip() if name_el else "Unknown"

                title_el = card.query_selector(
                    "span.mn-connection-card__occupation, span.t-14"
                )
                title = title_el.inner_text().strip() if title_el else ""

                seen_urls.add(profile_url)
                connections.append({
                    "profile_url": profile_url,
                    "name": name,
                    "title": title,
                    "company": "",
                    "first_seen": datetime.now().date().isoformat(),
                })

            except Exception as e:
                print(f"[scraper] connection parse error: {e}", file=sys.stderr)
                continue

        page.evaluate("window.scrollBy(0, 1000)")
        time.sleep(2)
        scroll_count += 1

    print(f"[scraper] connections: {len(connections)} collected", file=sys.stderr)
    return connections


if __name__ == "__main__":
    import json
    with sync_playwright() as p:
        context = setup_browser(p)
        page = context.new_page()
        posts = scrape_feed(page)
        connections = scrape_connections(page)
        context.close()
    print(json.dumps({"posts": posts, "connections": connections}))
