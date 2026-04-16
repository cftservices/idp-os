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
sys.path.insert(0, str(Path(__file__).parent.parent))
from classifier import classify_post, classify_connection
from store import LinkedInStore
import message_store as ms


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


def run_scrape_all_connections():
    """Scrape ALL LinkedIn connections and persist them in ChromaDB."""
    print(f"[run] Starting full connections scrape — {datetime.now().isoformat()}")

    cmd = [
        sys.executable,
        str(Path(__file__).parent / "linkedin_scraper.py"),
        "--scrape-all-connections",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        print(f"[run] Scraper stderr:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    stdout_lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    if not stdout_lines:
        print("[run] No output from scraper", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(stdout_lines[-1])
    except json.JSONDecodeError as e:
        print(f"[run] Failed to parse scraper output: {e}", file=sys.stderr)
        sys.exit(1)

    all_conns = data.get("all_connections", [])
    print(f"[run] Scraped {len(all_conns)} connections from LinkedIn")

    store = LinkedInStore(CHROMA_PATH)
    saved = 0
    for conn in all_conns:
        if not conn.get("profile_url"):
            continue
        # Preserve existing classification if already in DB
        existing = store.get_connection(conn["profile_url"])
        if existing and existing.get("classification") not in (None, "", "unknown"):
            conn["classification"] = existing["classification"]
        store.upsert_connection(conn)
        saved += 1

    print(f"[run] Done. {saved} connections stored in ChromaDB.")


def run_scrape_messages():
    """Scrape LinkedIn DM inbox for outgoing messages and persist them via message_store."""
    print(f"[run] Starting DM message scrape — {datetime.now().isoformat()}")

    cmd = [
        sys.executable,
        str(Path(__file__).parent / "linkedin_scraper.py"),
        "--scrape-messages",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"[run] Scraper stderr:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

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

    conversations = data.get("messages", [])
    print(f"[run] Found {len(conversations)} conversation(s) in DM inbox")

    total_saved = 0
    for conv in conversations:
        profile_url = conv.get("profile_url", "")
        name = conv.get("name", "")
        msgs = conv.get("messages", [])
        if not profile_url:
            continue
        saved = ms.save_scraped_messages(profile_url, name, "", msgs)
        total_saved += saved
        print(f"[run] {name} ({profile_url}): {saved} new message(s) saved")

    print(f"[run] Done. Total new messages saved: {total_saved}")


def run_enrich_targets(queue_path: str = "") -> None:
    """Enrich About/Info text for FB outreach targets (or all connections without about)."""
    print(f"[run] Starting About enrichment — {datetime.now().isoformat()}")

    store = LinkedInStore(CHROMA_PATH)

    # Determine which URLs to enrich
    if queue_path:
        import json as _json
        with open(queue_path, encoding="utf-8") as _f:
            queue = _json.load(_f)
        urls = [item["profile_url"] for item in queue if not item.get("sent")]
    else:
        # Fall back: all connections without about text
        all_conns = store.get_all_connections()
        urls = [c["profile_url"] for c in all_conns if not c.get("about")]

    print(f"[run] {len(urls)} profiles to enrich")

    # Enrich in batches via scraper subprocess (reuses existing enrich_connections())
    import json as _json
    BATCH = 30
    total_enriched = 0
    for i in range(0, len(urls), BATCH):
        batch = urls[i:i + BATCH]
        print(f"[run] Enriching batch {i // BATCH + 1}: {len(batch)} profiles...")
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "linkedin_scraper.py"),
                "--enrich-urls", _json.dumps(batch),
            ],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            print(f"[run] Scraper error:\n{result.stderr}", file=sys.stderr)
            continue

        stdout_lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        if not stdout_lines:
            continue
        try:
            data = _json.loads(stdout_lines[-1])
        except Exception:
            continue

        about_results: dict = data.get("about", {})
        for profile_url, about_text in about_results.items():
            if about_text:
                store.update_connection_about(profile_url, about_text)
                total_enriched += 1
        print(f"[run] Batch done — {sum(1 for t in about_results.values() if t)} enriched")

    print(f"[run] Done. Total About texts stored: {total_enriched}")


def run_send_dms(queue_path: str = "") -> None:
    """Send LinkedIn DMs via Playwright based on a queue JSON file."""
    print(f"[run] Starting DM send — {datetime.now().isoformat()}")

    if not queue_path:
        queue_path = str(Path(REPORT_OUTPUT) / "fb-community-queue.json")

    if not Path(queue_path).exists():
        print(f"[run] Queue file not found: {queue_path}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "linkedin_scraper.py"),
            "--send-dms", queue_path,
        ],
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        print(f"[run] Scraper stderr:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Print scraper progress (stderr was captured but logged live to terminal
    # by the scraper itself writing to its own stderr)
    for line in result.stderr.splitlines():
        print(line)
    print("[run] DM send complete.")


if __name__ == "__main__":
    import argparse as _argparse

    _parser = _argparse.ArgumentParser()
    _parser.add_argument("--scrape-messages", action="store_true", default=False,
                         help="Scrape DM inbox outgoing messages and save to message store")
    _parser.add_argument("--scrape-all-connections", action="store_true", default=False,
                         help="Scrape all 700+ LinkedIn connections and store in ChromaDB")
    _parser.add_argument("--enrich-targets", action="store_true", default=False,
                         help="Bulk-enrich About/Info text for FB outreach targets")
    _parser.add_argument("--send-dms", metavar="QUEUE_JSON", nargs="?", const="",
                         default=None,
                         help="Send DMs via Playwright using queue JSON (default: fb-community-queue.json)")
    _args = _parser.parse_args()

    if _args.scrape_messages:
        run_scrape_messages()
    elif _args.scrape_all_connections:
        run_scrape_all_connections()
    elif _args.enrich_targets:
        run_enrich_targets()
    elif _args.send_dms is not None:
        run_send_dms(_args.send_dms or "")
    else:
        main()
