from __future__ import annotations
import json
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


def scrape_keyword(context: BrowserContext, keyword: str, date_filter: str = "past-24h") -> list[dict]:
    """
    Search LinkedIn for posts matching keyword.
    date_filter: 'past-24h' | 'past-week' | 'past-month'
    Returns list of posts with url, text, author_name, author_profile_url, timestamp.
    """
    page = context.new_page()
    try:
        # Intercept clipboard.writeText to capture post URLs
        captured_urls: list[str] = []
        page.expose_function("captureClipboard", lambda url: captured_urls.append(url))

        search_url = (
            f"https://www.linkedin.com/search/results/content/"
            f"?keywords={keyword}&sortBy=%22date_posted%22&datePosted=%22{date_filter}%22"
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
    """Scrape first-degree connections list.

    LinkedIn's UI uses fully obfuscated CSS classes — no stable selectors.
    Strategy: find all a[href*='/in/'] links, walk up the DOM to find a
    container with name+title text, parse lines to extract structured data.
    """
    # Read extraction JS from sibling file
    js_path = Path(__file__).parent / "inspect_conn.js"
    extract_js = js_path.read_text(encoding="utf-8")

    page = context.new_page()
    try:
        page.goto(
            "https://www.linkedin.com/mynetwork/invite-connect/connections/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        time.sleep(5)

        connections: list[dict] = []
        seen_urls: set[str] = set()
        no_new_count = 0

        for scroll_round in range(40):
            cards = page.evaluate(extract_js)

            new_this_round = 0
            for card in cards:
                url = card.get("profile_url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                new_this_round += 1
                connections.append({
                    "profile_url": url,
                    "name": card.get("name", "Unknown"),
                    "title": card.get("title", ""),
                    "company": "",
                    "first_seen": datetime.now().date().isoformat(),
                })

            if new_this_round == 0:
                no_new_count += 1
                if no_new_count >= 3:
                    break  # end of list
            else:
                no_new_count = 0

            page.evaluate("window.scrollBy(0, 1200)")
            time.sleep(1.5)

        print(f"[scraper] connections: {len(connections)} collected", file=sys.stderr)
        return connections
    finally:
        page.close()


def scrape_profile_about(context: BrowserContext, profile_url: str) -> str:
    """Visit a LinkedIn profile and extract the About section text (up to 500 chars).
    Returns empty string on any failure.
    """
    page = context.new_page()
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        about = page.evaluate("""() => {
            const anchor = document.querySelector('#about');
            if (!anchor) return '';
            let el = anchor.parentElement;
            for (let i = 0; i < 6; i++) {
                if (!el) break;
                const text = (el.innerText || '').trim();
                if (text.length > 50) {
                    const lines = text.split('\\n')
                        .map(l => l.trim())
                        .filter(l => l && l !== 'About' && l !== 'Over');
                    return lines.join(' ').slice(0, 500);
                }
                el = el.parentElement;
            }
            return '';
        }""")
        return about or ""
    except Exception as e:
        print(f"[scraper] about error for {profile_url}: {e}", file=sys.stderr)
        return ""
    finally:
        page.close()


def enrich_connections(context: BrowserContext, urls: list[str]) -> dict[str, str]:
    """Visit each profile URL and extract About text. Returns {profile_url: about_text}.
    Tolerates per-URL failures.
    """
    results: dict[str, str] = {}
    for url in urls:
        results[url] = scrape_profile_about(context, url)
        print(f"[scraper] enriched {url}: {len(results[url])} chars", file=sys.stderr)
        time.sleep(2)
    return results


def scrape_engagement(context: BrowserContext, post_url: str) -> list[dict]:
    """Scrape likers and commenters from a LinkedIn post.
    Returns list of {profile_url, name, title} dicts.
    """
    page = context.new_page()
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        people: list[dict] = []
        seen_urls: set[str] = set()

        # ── Commenters ────────────────────────────────────────────────────────
        comment_cards = page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href*="/in/"]'));
            const seen = new Set();
            const cards = [];
            for (const link of links) {
                const href = link.href.split('?')[0];
                if (!href.includes('/in/') || seen.has(href)) continue;
                seen.add(href);
                const name = (link.innerText || link.textContent || '').trim().split('\\n')[0].trim();
                if (!name || name.length < 2) continue;
                cards.push({profile_url: href, name: name, title: ''});
            }
            return cards;
        }""")
        for card in comment_cards:
            url = card.get("profile_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                people.append(card)

        # ── Likers modal ──────────────────────────────────────────────────────
        reactions_btn = page.query_selector(
            'button[aria-label*="eaction"], '
            'span[aria-label*="eaction"]'
        )
        if reactions_btn:
            reactions_btn.click()
            time.sleep(2)
            for _ in range(15):
                modal_cards = page.evaluate("""() => {
                    const seen = new Set();
                    const cards = [];
                    const dialogs = document.querySelectorAll('[role="dialog"]');
                    for (const dialog of dialogs) {
                        const links = Array.from(dialog.querySelectorAll('a[href*="/in/"]'));
                        for (const link of links) {
                            const href = link.href.split('?')[0];
                            if (!href.includes('/in/') || seen.has(href)) continue;
                            seen.add(href);
                            const name = (link.innerText || '').trim().split('\\n')[0].trim();
                            cards.push({profile_url: href, name: name, title: ''});
                        }
                    }
                    return cards;
                }""")
                new_count = 0
                for card in modal_cards:
                    url = card.get("profile_url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        people.append(card)
                        new_count += 1
                page.evaluate("""() => {
                    const d = document.querySelector('[role="dialog"]');
                    if (d) d.scrollBy(0, 800);
                }""")
                time.sleep(1)
                if new_count == 0:
                    break
            page.keyboard.press("Escape")
            time.sleep(0.5)

        print(f"[scraper] engagement {post_url}: {len(people)} people", file=sys.stderr)
        return people

    except Exception as e:
        print(f"[scraper] engagement error for {post_url}: {e}", file=sys.stderr)
        return []
    finally:
        page.close()


def scrape_all_connections(context: BrowserContext) -> list[dict]:
    """
    Scrape ALL LinkedIn connections from /mynetwork/invite-connect/connections/.
    Returns list of {profile_url, name, title, company}.
    Uses broad link-scanning approach to survive LinkedIn DOM changes.
    """
    page = context.new_page()
    results: list[dict] = []
    seen_urls: set[str] = set()

    try:
        page.goto(
            "https://www.linkedin.com/mynetwork/invite-connect/connections/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        time.sleep(6)

        title = page.title()
        print(f"[scrape_all_connections] Page: {title}", file=sys.stderr)

        no_growth_streak = 0
        prev_count = -1
        scroll_round = 0
        max_rounds = 300

        # Move mouse to center of page so mouse.wheel works on the right element
        page.mouse.move(800, 400)

        while scroll_round < max_rounds:
            # Extract all /in/ links. LinkedIn renders each connection with 2 links:
            # - link 1: image link (empty text)
            # - link 2: text link with "Name\n\nOccupation"
            # We take only links with non-empty text and parse Name + Title from it.
            links = page.evaluate("""() => {
                const seen = new Set();
                const results = [];
                document.querySelectorAll('a[href*="/in/"]').forEach(link => {
                    const href = link.href;
                    if (!href || !href.includes('/in/')) return;
                    const clean = href.split('?')[0].replace(/\\/+$/, '') + '/';
                    // Only basic /in/slug/ URLs (skip /in/slug/detail/ etc)
                    const parts = clean.replace('https://www.linkedin.com', '').split('/').filter(Boolean);
                    if (parts.length !== 2 || parts[0] !== 'in') return;
                    if (seen.has(clean)) return;
                    const text = link.innerText.trim();
                    if (!text) return;  // Skip image-only links
                    seen.add(clean);
                    // text format: "Name\\n\\nOccupation" or just "Name"
                    const parts2 = text.split('\\n\\n');
                    results.push({
                        profile_url: clean,
                        name: parts2[0].trim(),
                        title: parts2.slice(1).join(' ').trim(),
                    });
                });
                return results;
            }""")

            for item in links:
                url = item.get("profile_url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                results.append({
                    "profile_url": url,
                    "name": item.get("name", ""),
                    "title": item.get("title", ""),
                    "company": "",
                    "classification": "unknown",
                })

            current_count = len(results)
            if scroll_round % 10 == 0 or current_count != prev_count:
                print(f"[scrape_all_connections] {current_count} connections (round {scroll_round})", file=sys.stderr)

            if current_count == prev_count:
                no_growth_streak += 1
                if no_growth_streak >= 6:
                    print("[scrape_all_connections] No new connections for 6 rounds — done", file=sys.stderr)
                    break
            else:
                no_growth_streak = 0

            prev_count = current_count

            # Use mouse wheel events — these are the only scroll method LinkedIn's
            # virtual scroll actually responds to (scrollTop assignments are ignored).
            # Scroll 3000px per round (LinkedIn loads ~10 connections per batch).
            page.mouse.wheel(0, 3000)
            time.sleep(2.5)
            scroll_round += 1

    except Exception as e:
        print(f"[scrape_all_connections] Fatal error: {e}", file=sys.stderr)
    finally:
        page.close()

    print(f"[scrape_all_connections] Done — {len(results)} connections total", file=sys.stderr)
    return results


def scrape_messages(context: BrowserContext) -> list[dict]:
    """
    Scrape LinkedIn DM inbox outgoing messages only.
    Returns list of {profile_url, name, messages: [{date, type, content}]}
    """
    page = context.new_page()
    results: list[dict] = []
    try:
        page.goto("https://www.linkedin.com/messaging/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # Build name→canonical-URL lookup from ChromaDB so we can resolve ACoAAA URLs to slugs
        name_to_url: dict[str, str] = {}
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent.parent))
            from store import LinkedInStore as _Store
            import os as _os
            _chroma = _os.getenv("CHROMA_PATH", "c:/tools/linkedin-intel/db/chroma")
            _store = _Store(_chroma)
            for conn in _store.get_all_connections():
                _name = conn.get("name", "").strip().lower()
                _url = conn.get("profile_url", "")
                if _name and _url:
                    name_to_url[_name] = _url
            print(f"[scrape_messages] Loaded {len(name_to_url)} connection names for URL resolution", file=sys.stderr)
        except Exception as _e:
            print(f"[scrape_messages] Could not load ChromaDB for name resolution: {_e}", file=sys.stderr)

        thread_items = page.query_selector_all("li.msg-conversation-listitem")
        print(f"[scrape_messages] Found {len(thread_items)} conversation threads", file=sys.stderr)

        for idx in range(len(thread_items)):
            try:
                # Re-query each iteration — DOM may re-render after click
                items = page.query_selector_all("li.msg-conversation-listitem")
                if idx >= len(items):
                    break
                item = items[idx]

                # Extract name from thread item (works reliably)
                name_el = item.query_selector(".msg-conversation-listitem__participant-names")
                name = name_el.inner_text().strip() if name_el else ""

                # Click to open conversation — profile link appears in panel header
                item.click()
                time.sleep(2)

                # Get profile URL from conversation header (ACoAAA format from LinkedIn messaging)
                header_link = page.query_selector(".msg-thread__link-to-profile")
                aco_url = ""
                if header_link:
                    href = header_link.get_attribute("href") or ""
                    if href.startswith("/"):
                        aco_url = "https://www.linkedin.com" + href.split("?")[0].rstrip("/") + "/"
                    else:
                        aco_url = href.split("?")[0].rstrip("/") + "/"

                # Prefer canonical slug URL from ChromaDB (matched by name); fall back to ACoAAA
                profile_url = name_to_url.get(name.strip().lower(), aco_url)
                if not profile_url:
                    print(f"[scrape_messages] No profile URL for '{name}' — skipping", file=sys.stderr)
                    continue

                # Scrape outgoing messages (sent by self — no "other" class)
                sent_messages: list[dict] = []

                conv_panel = page.query_selector(".msg-s-message-list")
                if conv_panel:
                    page.evaluate("(el) => el.scrollTop = 0", conv_panel)
                    time.sleep(1)

                msg_events = page.query_selector_all(".msg-s-event-listitem")
                for ev in msg_events:
                    # Skip messages from the other person
                    ev_class = ev.get_attribute("class") or ""
                    if "msg-s-event-listitem--other" in ev_class:
                        continue

                    # LinkedIn changed content container from .msg-s-event__content
                    # to .msg-s-event-listitem__message-bubble
                    body = ev.query_selector(
                        ".msg-s-event-listitem__message-bubble, .msg-s-event__content"
                    )
                    if not body:
                        continue
                    content = body.inner_text().strip()
                    if not content:
                        continue

                    time_el = ev.query_selector("time")
                    if time_el:
                        date_str = (time_el.get_attribute("datetime") or datetime.now().date().isoformat())[:10]
                    else:
                        date_str = datetime.now().date().isoformat()

                    sent_messages.append({
                        "date": date_str,
                        "type": "dm",
                        "content": content,
                    })

                if sent_messages or name:
                    results.append({
                        "profile_url": profile_url,
                        "name": name,
                        "messages": sent_messages,
                    })
                    print(f"[scrape_messages] {name}: {len(sent_messages)} outgoing message(s)", file=sys.stderr)

            except Exception as e:
                print(f"[scrape_messages] Thread {idx} error: {e}", file=sys.stderr)
                continue

    except Exception as e:
        print(f"[scrape_messages] Fatal error: {e}", file=sys.stderr)
    finally:
        page.close()

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("date_filter", nargs="?", default="past-24h")
    parser.add_argument("--enrich-urls", default="[]", help="JSON list of profile URLs to enrich with About text")
    parser.add_argument("--engagement-urls", default="[]", help="JSON list of post URLs to scrape likes/comments for")
    parser.add_argument("--scrape-messages", action="store_true", default=False)
    parser.add_argument("--scrape-all-connections", action="store_true", default=False)
    args = parser.parse_args()

    try:
        enrich_urls: list[str] = json.loads(args.enrich_urls)
        engagement_urls: list[str] = json.loads(args.engagement_urls)
    except json.JSONDecodeError as e:
        print(f"[scraper] Invalid JSON in --enrich-urls or --engagement-urls: {e}", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        context = setup_browser(p)

        all_posts: list[dict] = []
        seen_post_urls: set[str] = set()

        # When --scrape-messages or --scrape-all-connections is set, skip keyword scraping
        connections: list[dict] = []
        all_connections_full: list[dict] = []

        if args.scrape_all_connections:
            all_connections_full = scrape_all_connections(context)

        if not args.scrape_messages and not args.scrape_all_connections:
            date_filter = args.date_filter
            kw_limit = None if date_filter != "past-24h" else 8
            print(f"[scraper] date_filter={date_filter}, keywords={kw_limit or 'all'}", file=sys.stderr)

            for keyword in (KEYWORDS[:kw_limit] if kw_limit else KEYWORDS):
                kw_posts = scrape_keyword(context, keyword, date_filter=date_filter)
                for post in kw_posts:
                    if post["url"] not in seen_post_urls:
                        seen_post_urls.add(post["url"])
                        all_posts.append(post)
                time.sleep(4)

            connections = scrape_connections(context)

        # Enrich profile About sections
        about_results: dict[str, str] = {}
        if enrich_urls:
            print(f"[scraper] enriching {len(enrich_urls)} profiles...", file=sys.stderr)
            about_results = enrich_connections(context, enrich_urls)

        # Scrape engagement (likers + commenters) for specified posts
        engagement_results: dict[str, list[dict]] = {}
        if engagement_urls:
            print(f"[scraper] scraping engagement for {len(engagement_urls)} posts...", file=sys.stderr)
            for post_url in engagement_urls:
                engagement_results[post_url] = scrape_engagement(context, post_url)
                time.sleep(3)

        # Scrape DM inbox outgoing messages
        if args.scrape_messages:
            messages_results: list[dict] = scrape_messages(context)
        else:
            messages_results = []

        context.close()

    print(json.dumps({
        "posts": all_posts,
        "connections": connections,
        "all_connections": all_connections_full,
        "about": about_results,
        "engagement": engagement_results,
        "messages": messages_results,
    }))
