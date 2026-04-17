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

KW_LIMIT = 3        # max keywords per run (detection reduction)
POSTS_PER_KW = 8    # max posts per keyword to scrape
NEW_CONN_LIMIT = 10 # max new connections to process per run
MSG_DAYS = 2        # scrape DM inbox for last N days each run

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from classifier import classify_post, classify_connection
from store import LinkedInStore
import message_store as ms


def main():
    date_filter = sys.argv[1] if len(sys.argv) > 1 else "past-24h"
    print(f"[run] Starting LinkedIn Intel — {datetime.now().isoformat()} — filter: {date_filter}")

    # 1. Init DB — load known URLs so we can skip existing posts/connections
    store = LinkedInStore(chroma_path=CHROMA_PATH)
    known_post_urls = {p["url"] for p in store.get_all_posts()}
    known_conn_urls = {c["profile_url"] for c in store.get_all_connections()}
    print(f"[run] DB: {len(known_post_urls)} posts, {len(known_conn_urls)} connections known")

    # 2. Run Playwright scraper — limited keywords, no enrichment, no engagement
    print(f"[run] Launching scraper (max {KW_LIMIT} keywords, {POSTS_PER_KW} posts each)...")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "linkedin_scraper.py"),
            date_filter,
            "--kw-limit", str(KW_LIMIT),
            "--posts-per-kw", str(POSTS_PER_KW),
            "--enrich-urls", json.dumps([]),
            "--engagement-urls", json.dumps([]),
        ],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"[run] Scraper stderr:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    # 3. Parse JSON output
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
    print(f"[run] Scraped {len(raw_posts)} posts, {len(raw_connections)} connections")

    # 4. Only process NEW connections (not yet in DB) — enrich About immediately
    new_conns = [c for c in raw_connections if c.get("profile_url") and c["profile_url"] not in known_conn_urls]
    new_conns = new_conns[:NEW_CONN_LIMIT]
    print(f"[run] {len(new_conns)} new connections (skipping {len(raw_connections) - len(new_conns)} known)")

    if new_conns:
        new_urls = [c["profile_url"] for c in new_conns]
        print(f"[run] Enriching About for {len(new_urls)} new connections...")
        enrich_result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "linkedin_scraper.py"),
                "--enrich-urls", json.dumps(new_urls),
            ],
            capture_output=True, text=True, timeout=300,
        )
        about_results: dict[str, str] = {}
        if enrich_result.returncode == 0:
            enrich_lines = [l for l in enrich_result.stdout.strip().split("\n") if l.strip()]
            if enrich_lines:
                try:
                    about_results = json.loads(enrich_lines[-1]).get("about", {})
                except Exception:
                    pass
        else:
            print(f"[run] Enrichment stderr:\n{enrich_result.stderr[:500]}", file=sys.stderr)

        for conn in new_conns:
            url = conn["profile_url"]
            conn["classification"] = classify_connection(conn, IDEAL_CLIENT_TITLES, COLLEAGUE_NAMES, INFLUENCER_KEYWORDS)
            if url in about_results and about_results[url]:
                conn["about"] = about_results[url]
            store.upsert_connection(conn)
            print(f"[run] New connection: {conn.get('name','?')} — {conn['classification']}")

    # 5. Only store NEW posts (not yet in DB)
    classified_posts = []
    new_post_count = 0
    for post in raw_posts:
        if post.get("url") in known_post_urls:
            continue
        classified = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
        store.add_post(classified)
        new_post_count += 1
        if classified.get("author_profile_url"):
            store.increment_post_count(classified["author_profile_url"])
        if classified["classification"] != "neutral":
            classified_posts.append(classified)

    print(f"[run] {new_post_count} new posts stored, {len(classified_posts)} relevant (non-neutral)")

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

    # 12. Scrape DM inbox for last MSG_DAYS days and update message store
    print(f"[run] Scraping DM inbox (last {MSG_DAYS} days)...")
    msg_result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "linkedin_scraper.py"),
            "--scrape-messages",
            "--days", str(MSG_DAYS),
        ],
        capture_output=True, text=True, timeout=300,
    )
    if msg_result.returncode != 0:
        print(f"[run] Message scraper error:\n{msg_result.stderr[:500]}", file=sys.stderr)
    else:
        msg_lines = [l for l in msg_result.stdout.strip().split("\n") if l.strip()]
        if msg_lines:
            try:
                msg_data = json.loads(msg_lines[-1])
                conversations = msg_data.get("messages", [])
                total_saved = 0
                for conv in conversations:
                    profile_url = conv.get("profile_url", "")
                    name = conv.get("name", "")
                    msgs = conv.get("messages", [])
                    if not profile_url:
                        continue
                    saved = ms.save_scraped_messages(profile_url, name, "", msgs)
                    if saved:
                        print(f"[run] {name}: {saved} new message(s)")
                    total_saved += saved
                print(f"[run] DM inbox: {total_saved} new messages saved")
            except Exception as e:
                print(f"[run] Failed to parse message output: {e}", file=sys.stderr)

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


def run_scrape_messages(days: int = 0):
    """Scrape LinkedIn DM inbox for outgoing messages and persist them via message_store.
    days: if > 0, only process messages from the last N days.
    """
    days_label = f"last {days} days" if days > 0 else "all time"
    print(f"[run] Starting DM message scrape ({days_label}) — {datetime.now().isoformat()}")

    cmd = [
        sys.executable,
        str(Path(__file__).parent / "linkedin_scraper.py"),
        "--scrape-messages",
    ]
    if days > 0:
        cmd += ["--days", str(days)]

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

    ENRICH_TARGET_LIMIT = 30  # max profiles per run to avoid LinkedIn detection

    # Determine which URLs to enrich
    import json as _json
    if queue_path:
        with open(queue_path, encoding="utf-8") as _f:
            queue = _json.load(_f)
        urls = [item["profile_url"] for item in queue if not item.get("sent")]
    else:
        # Only connections without about text, prioritise ideal_client/influencer
        all_conns = store.get_all_connections()
        without_about = [c for c in all_conns if not c.get("about")]
        priority = [c for c in without_about if c.get("classification") in ("ideal_client", "influencer")]
        rest = [c for c in without_about if c not in priority]
        urls = [c["profile_url"] for c in (priority + rest)]

    urls = urls[:ENRICH_TARGET_LIMIT]
    print(f"[run] {len(urls)} profiles queued for enrichment (capped at {ENRICH_TARGET_LIMIT})")

    # Enrich all in ONE subprocess call (one browser, no repeated launches)
    total_enriched = 0
    if urls:
        print(f"[run] Enriching {len(urls)} profiles in one browser session...")
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "linkedin_scraper.py"),
                "--enrich-urls", _json.dumps(urls),
            ],
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            print(f"[run] Scraper error:\n{result.stderr}", file=sys.stderr)
        else:
            stdout_lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
            if stdout_lines:
                try:
                    data = _json.loads(stdout_lines[-1])
                    about_results: dict = data.get("about", {})
                    for profile_url, about_text in about_results.items():
                        if about_text:
                            store.update_connection_about(profile_url, about_text)
                            total_enriched += 1
                    print(f"[run] Enriched {total_enriched} profiles")
                except Exception:
                    pass

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
    _parser.add_argument("--days", type=int, default=3,
                         help="With --scrape-messages: only process messages from last N days (default: 3, 0=all)")
    _parser.add_argument("--scrape-all-connections", action="store_true", default=False,
                         help="Scrape all 700+ LinkedIn connections and store in ChromaDB")
    _parser.add_argument("--enrich-targets", action="store_true", default=False,
                         help="Bulk-enrich About/Info text for FB outreach targets")
    _parser.add_argument("--send-dms", metavar="QUEUE_JSON", nargs="?", const="",
                         default=None,
                         help="Send DMs via Playwright using queue JSON (default: fb-community-queue.json)")
    _args = _parser.parse_args()

    if _args.scrape_messages:
        run_scrape_messages(days=_args.days)
    elif _args.scrape_all_connections:
        run_scrape_all_connections()
    elif _args.enrich_targets:
        run_enrich_targets()
    elif _args.send_dms is not None:
        run_send_dms(_args.send_dms or "")
    else:
        main()
