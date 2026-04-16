#!/usr/bin/env python3
"""Migrate messages/*.json files into ChromaDB, then delete the JSON files."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "scraper" / ".env")

CHROMA_PATH = os.getenv("CHROMA_PATH", str(Path(__file__).parent / "db" / "chroma"))
MESSAGES_DIR = Path(__file__).parent / "messages"

sys.path.insert(0, str(Path(__file__).parent / "scraper"))
from store import LinkedInStore


def main() -> None:
    store = LinkedInStore(chroma_path=CHROMA_PATH)
    json_files = list(MESSAGES_DIR.glob("*.json"))

    if not json_files:
        print("[migrate] No JSON files found in messages/. Nothing to do.")
        return

    print(f"[migrate] Found {len(json_files)} conversation file(s).")
    total_saved = 0
    total_files = 0

    for path in sorted(json_files):
        try:
            with open(path, encoding="utf-8") as f:
                conv = json.load(f)
        except Exception as e:
            print(f"[migrate] SKIP {path.name}: {e}")
            continue

        profile_url = conv.get("profile_url", "")
        name = conv.get("name", "")
        title = conv.get("title", "")
        messages = conv.get("messages", [])

        if not profile_url:
            print(f"[migrate] SKIP {path.name}: no profile_url")
            continue

        try:
            saved = store.save_scraped_messages(profile_url, name, title, messages)
        except Exception as e:
            print(f"[migrate] SKIP {path.name}: store error: {e}")
            continue

        print(f"[migrate] {name or path.stem}: {saved}/{len(messages)} message(s) saved -> {path.name}")
        total_saved += saved
        total_files += 1
        path.unlink()
        print(f"[migrate]   Deleted {path.name}")

    print(f"\n[migrate] Done. {total_files} file(s) processed, {total_saved} message(s) imported.")


if __name__ == "__main__":
    main()
