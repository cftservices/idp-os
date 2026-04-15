from __future__ import annotations
import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

CHROMA_PATH = os.getenv("CHROMA_PATH", "c:/tools/linkedin-intel/db/chroma")
REPORT_OUTPUT = os.getenv("REPORT_OUTPUT", "c:/tools/Basecamp-Compass/user-workspace/linkedin-feed")
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS", "").split(",") if k.strip()]
INFLUENCER_KEYWORDS = [k.strip() for k in os.getenv("INFLUENCER_KEYWORDS", "").split(",") if k.strip()]
IDEAL_CLIENT_TITLES = [k.strip() for k in os.getenv("IDEAL_CLIENT_TITLES", "").split(",") if k.strip()]
COLLEAGUE_NAMES = [k.strip() for k in os.getenv("COLLEAGUE_NAMES", "").split(",") if k.strip()]

ENRICH_LIMIT = 20   # max profiles to enrich per run
ENGAGE_LIMIT = 5    # max posts to scrape engagement for per run

sys.path.insert(0, str(Path(__file__).parent))
from classifier import classify_post, classify_connection
from store import LinkedInStore


def main():
    date_filter = sys.argv[1] if len(sys.argv) > 1 else "past-24h"
    print(f"[run] Starting LinkedIn Intel — {datetime.now().isoformat()} — filter: {date_filter}")

    # 1. Init DB early so we can query for enrichment/engagement URLs
    store = LinkedInStore(chroma_path=CHROMA_PATH)

    # 2. Determine which connections need About enrichment (no 'about' field yet)
    all_connections_pre = store.get_all_connections()
    to_enrich = [c["profile_url"] for c in all_connections_pre if not c.get("about")][:ENRICH_LIMIT]
    print(f"[run] {len(to_enrich)} connections queued for About enrichment")

    # 3. Determine which posts need engagement scraping (ideal_client/influencer, not yet scraped)
    all_posts_pre = store.get_all_posts()
    to_engage = [
        p["url"] for p in all_posts_pre
        if p.get("classification") in ("ideal_client", "influencer")
        and not p.get("engagement_scraped")
    ][:ENGAGE_LIMIT]
    print(f"[run] {len(to_engage)} posts queued for engagement scraping")

    # 4. Run Playwright scraper as subprocess (outputs JSON to stdout)
    print("[run] Launching Playwright scraper...")
    timeout = 1800 if date_filter == "past-week" else 600
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "linkedin_scraper.py"),
            date_filter,
            "--enrich-urls", json.dumps(to_enrich),
            "--engagement-urls", json.dumps(to_engage),
        ],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        print(f"[run] Scraper stderr:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    # 5. Parse JSON output
    stdout_lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    if not stdout_lines:
        print("[run] No output from scraper", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(stdout_lines[-1])
    except json.JSONDecodeError as e:
        print(f"[run] Failed to parse scraper output: {e}", file=sys.stderr)
        print(f"[run] Raw output: {stdout_lines[-1][:200]}", file=sys.stderr)
        sys.exit(1)

    raw_posts = data.get("posts", [])
    raw_connections = data.get("connections", [])
    about_results: dict[str, str] = data.get("about", {})
    engagement_results: dict[str, list] = data.get("engagement", {})

    print(f"[run] Scraped {len(raw_posts)} posts, {len(raw_connections)} connections")
    print(f"[run] About enrichments: {len(about_results)}, Engagement batches: {len(engagement_results)}")

    # 6. Classify and store connections
    for conn in raw_connections:
        conn["classification"] = classify_connection(conn, IDEAL_CLIENT_TITLES, COLLEAGUE_NAMES, INFLUENCER_KEYWORDS)
        store.upsert_connection(conn)

    # 7. Classify and store posts
    classified_posts = []
    for post in raw_posts:
        classified = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
        store.add_post(classified)
        if classified.get("author_profile_url"):
            store.increment_post_count(classified["author_profile_url"])
        if classified["classification"] != "neutral":
            classified_posts.append(classified)

    print(f"[run] {len(classified_posts)} relevant posts (non-neutral)")

    # 8. Store About enrichments
    for profile_url, about_text in about_results.items():
        if about_text:
            store.update_connection_about(profile_url, about_text)
    if about_results:
        enriched_count = sum(1 for t in about_results.values() if t)
        print(f"[run] Stored About text for {enriched_count} profiles")

    # 9. Store engagement (likers + commenters) as connections, mark posts as scraped
    total_people = 0
    for post_url, people in engagement_results.items():
        for person in people:
            if not person.get("profile_url"):
                continue
            conn = {
                "profile_url": person["profile_url"],
                "name": person.get("name", "Unknown"),
                "title": person.get("title", ""),
                "company": "",
                "first_seen": datetime.now().date().isoformat(),
            }
            conn["classification"] = classify_connection(conn, IDEAL_CLIENT_TITLES, COLLEAGUE_NAMES, INFLUENCER_KEYWORDS)
            store.upsert_connection(conn)
            total_people += 1
        # Mark post as engagement-scraped
        store.mark_engagement_scraped(post_url)
    if engagement_results:
        print(f"[run] Stored {total_people} engagement contacts from {len(engagement_results)} posts")

    # 10. Build connections lookup for sidecar
    connections_lookup: dict[str, dict] = {}
    for post in classified_posts:
        url = post.get("author_profile_url", "")
        if url:
            conn = store.get_connection(url)
            if conn:
                connections_lookup[url] = conn

    # 11. Write JSON sidecar for Claude Code /linkedin-leads skill
    today = datetime.now().date().isoformat()
    output_dir = Path(REPORT_OUTPUT)
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = output_dir / f"{today}-raw.json"

    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": today,
            "posts": classified_posts,
            "connections": connections_lookup,
        }, f, ensure_ascii=False, indent=2)

    print(f"[run] Raw data saved to {sidecar_path}")
    print("[run] Done. Open Claude Code and run /linkedin-leads to generate your report.")


if __name__ == "__main__":
    main()
