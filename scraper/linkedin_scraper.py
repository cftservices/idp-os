from __future__ import annotations
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, BrowserContext

load_dotenv(Path(__file__).parent / ".env")

CHROMIUM_PROFILE = os.getenv("CHROMIUM_PROFILE", "c:/tools/linkedin-intel/browser-profile")
MAX_POSTS = int(os.getenv("MAX_POSTS", "150"))
KEYWORDS = [k.strip() for k in os.getenv(
    "KEYWORDS",
    "MQTT,OPC-UA,SCADA,PLC,historian,IIoT,Industry 4.0,UNS,data platform,Siemens,Ignition,AVEVA,HighByte,digital twin,edge computing,Grafana,MongoDB,Docker,step7,TIA Portal,WinCC,Profinet,Modbus,industrial automation,system integrator"
).split(",") if k.strip()]


def setup_browser(playwright) -> BrowserContext:
    """Launch Chromium with persistent profile (stays logged in)."""
    return playwright.chromium.launch_persistent_context(
        user_data_dir=CHROMIUM_PROFILE,
        headless=False,
        args=["--start-maximized"],
        no_viewport=True,
        permissions=["clipboard-read", "clipboard-write"],
    )


def scrape_keyword(context: BrowserContext, keyword: str) -> list[dict]:
    """
    Search LinkedIn for posts matching keyword posted in last 24h.
    Returns list of posts with url, text, author_name, author_profile_url, timestamp.
    """
    page = context.new_page()
    try:
        # Intercept clipboard.writeText to capture post URLs
        captured_urls: list[str] = []
        page.expose_function("captureClipboard", lambda url: captured_urls.append(url))

        search_url = (
            f"https://www.linkedin.com/search/results/content/"
            f"?keywords={keyword}&sortBy=%22date_posted%22&datePosted=%22past-24h%22"
        )
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)

        # Override clipboard.writeText to capture URLs
        page.evaluate("""() => {
            const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
            navigator.clipboard.writeText = async (text) => {
                window.captureClipboard(text);
                return orig(text);
            };
        }""")

        page.evaluate("window.scrollBy(0, 400)")
        time.sleep(2)

        posts = []
        seen_urls: set[str] = set()

        # Find all post menu buttons
        menu_buttons = page.query_selector_all(
            'button[aria-label*="Bedieningsmenu openen voor bijdrage"], '
            'button[aria-label*="Open control menu for post by"], '
            'button[aria-label*="Open control menu for contribution"]'
        )
        print(f"[scraper] '{keyword}': {len(menu_buttons)} posts found", file=sys.stderr)

        for btn in menu_buttons[:MAX_POSTS]:
            try:
                label = btn.get_attribute("aria-label") or ""
                # Extract author name from aria-label
                author_name = label
                for prefix in [
                    "Bedieningsmenu openen voor bijdrage van ",
                    "Open control menu for post by ",
                    "Open control menu for contribution by ",
                ]:
                    author_name = author_name.replace(prefix, "")

                # Find author profile URL — nearest /in/ link in the post container
                author_profile_url = ""
                try:
                    container = btn.evaluate_handle(
                        "el => { let p = el; for(let i=0;i<15;i++){p=p.parentElement; if(!p) break; if(p.querySelectorAll('a[href*=\"/in/\"]').length) return p;} return null; }"
                    ).as_element()
                    if container:
                        author_link = container.query_selector("a[href*='/in/']")
                        if author_link:
                            href = author_link.get_attribute("href") or ""
                            author_profile_url = href.split("?")[0]
                            if author_profile_url.startswith("/"):
                                author_profile_url = "https://www.linkedin.com" + author_profile_url
                except Exception:
                    pass

                # Get post text from container
                post_text = ""
                try:
                    container2 = btn.evaluate_handle(
                        "el => { let p = el; for(let i=0;i<15;i++){p=p.parentElement; if(!p) break; const t=(p.innerText||'').trim(); if(t.length>100) return p;} return null; }"
                    ).as_element()
                    if container2:
                        raw = container2.inner_text() or ""
                        # Clean up: take first substantial block
                        lines = [l.strip() for l in raw.split("\n") if len(l.strip()) > 30]
                        post_text = " ".join(lines[:5])[:800]
                except Exception:
                    pass

                # Click ... menu and copy link
                pre_count = len(captured_urls)
                btn.click()
                time.sleep(1.2)

                copy_btn = page.query_selector(
                    'p:text("Link naar bijdrage kopiëren"), '
                    'span:text("Link naar bijdrage kopiëren"), '
                    'li:text("Copy link to post"), '
                    'li:text("Link naar bijdrage kopiëren")'
                )
                post_url = ""
                if copy_btn:
                    copy_btn.click()
                    time.sleep(0.8)
                    if len(captured_urls) > pre_count:
                        post_url = captured_urls[-1].split("?")[0]  # strip tracking params

                page.keyboard.press("Escape")
                time.sleep(0.8)

                if not post_url or post_url in seen_urls:
                    continue

                seen_urls.add(post_url)
                posts.append({
                    "url": post_url,
                    "text": post_text,
                    "author_name": author_name,
                    "author_profile_url": author_profile_url,
                    "timestamp": datetime.now().isoformat(),  # search was filtered to past-24h
                    "keyword_source": keyword,
                })

            except Exception as e:
                print(f"[scraper] post error: {e}", file=sys.stderr)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                continue

        return posts

    finally:
        page.close()


def scrape_connections(context: BrowserContext) -> list[dict]:
    """Scrape first-degree connections list."""
    page = context.new_page()
    try:
        page.goto(
            "https://www.linkedin.com/mynetwork/invite-connect/connections/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        time.sleep(4)

        connections = []
        seen_urls: set[str] = set()

        for _ in range(20):
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

                    name_el = card.query_selector("span.mn-connection-card__name, span.t-16")
                    name = name_el.inner_text().strip() if name_el else "Unknown"

                    title_el = card.query_selector("span.mn-connection-card__occupation, span.t-14")
                    title = title_el.inner_text().strip() if title_el else ""

                    seen_urls.add(profile_url)
                    connections.append({
                        "profile_url": profile_url,
                        "name": name,
                        "title": title,
                        "company": "",
                        "first_seen": datetime.now().date().isoformat(),
                    })
                except Exception:
                    continue

            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(1.5)

        print(f"[scraper] connections: {len(connections)} collected", file=sys.stderr)
        return connections
    finally:
        page.close()


if __name__ == "__main__":
    import json

    with sync_playwright() as p:
        context = setup_browser(p)

        all_posts: list[dict] = []
        seen_post_urls: set[str] = set()

        for keyword in KEYWORDS[:8]:  # limit to 8 keywords per run to avoid rate limiting
            kw_posts = scrape_keyword(context, keyword)
            for post in kw_posts:
                if post["url"] not in seen_post_urls:
                    seen_post_urls.add(post["url"])
                    all_posts.append(post)
            time.sleep(3)  # pause between keyword searches

        connections = scrape_connections(context)
        context.close()

    print(json.dumps({"posts": all_posts, "connections": connections}))
