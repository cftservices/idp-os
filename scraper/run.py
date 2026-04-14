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

sys.path.insert(0, str(Path(__file__).parent))
from classifier import classify_post, classify_connection
from store import LinkedInStore


def main():
    print(f"[run] Starting LinkedIn Intel — {datetime.now().isoformat()}")

    # 1. Run Playwright scraper as subprocess (outputs JSON to stdout)
    print("[run] Launching Playwright scraper...")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "linkedin_scraper.py")],
        capture_output=True, text=True, timeout=600
    )
    if result.returncode != 0:
        print(f"[run] Scraper stderr:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Last line of stdout is the JSON payload
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

    # 2. Init DB
    store = LinkedInStore(chroma_path=CHROMA_PATH)

    # 3. Classify and store connections
    for conn in raw_connections:
        conn["classification"] = classify_connection(conn, IDEAL_CLIENT_TITLES, COLLEAGUE_NAMES)
        store.upsert_connection(conn)

    # 4. Classify and store posts
    classified_posts = []
    for post in raw_posts:
        classified = classify_post(post, KEYWORDS, INFLUENCER_KEYWORDS, COLLEAGUE_NAMES)
        store.add_post(classified)
        if classified.get("author_profile_url"):
            store.increment_post_count(classified["author_profile_url"])
        if classified["classification"] != "neutral":
            classified_posts.append(classified)

    print(f"[run] {len(classified_posts)} relevant posts (non-neutral)")

    # 5. Build connections lookup for sidecar
    connections_lookup: dict[str, dict] = {}
    for post in classified_posts:
        url = post.get("author_profile_url", "")
        if url:
            conn = store.get_connection(url)
            if conn:
                connections_lookup[url] = conn

    # 6. Write JSON sidecar for Claude Code /linkedin-leads skill
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
