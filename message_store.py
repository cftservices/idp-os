from __future__ import annotations
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "scraper" / ".env")

CHROMA_PATH = os.getenv("CHROMA_PATH", "c:/tools/linkedin-intel/db/chroma")

# Lazy singleton — replaced in tests via set_store()
_store = None


def _get_store():
    global _store
    if _store is None:
        sys.path.insert(0, str(Path(__file__).parent / "scraper"))
        from store import LinkedInStore
        _store = LinkedInStore(chroma_path=CHROMA_PATH)
    return _store


def set_store(store) -> None:
    """Inject a store instance (used in tests)."""
    global _store
    _store = store


def get_slug(profile_url: str) -> str:
    """Extract slug from LinkedIn profile URL."""
    match = re.search(r'/in/([^/?]+)', profile_url)
    if match:
        return match.group(1).rstrip("/")
    return re.sub(r'[^\w-]', '-', profile_url)[-50:]


def load_conversation(profile_url: str) -> dict:
    return _get_store().load_conversation(profile_url)


def save_message(profile_url: str, name: str, title: str, message: dict) -> None:
    _get_store().save_message(profile_url, name, title, message)


def save_scraped_messages(
    profile_url: str,
    name: str,
    title: str,
    messages: list[dict],
) -> int:
    return _get_store().save_scraped_messages(profile_url, name, title, messages)


def list_conversations() -> list[dict]:
    return _get_store().list_conversations()


def delete_message(profile_url: str, message_id: str) -> None:
    _get_store().delete_message(profile_url, message_id)


def build_clipboard_context(profile_url: str, chroma_posts: list[dict]) -> str:
    """Build structured context string for Claude Code clipboard."""
    conv = load_conversation(profile_url)
    name = conv.get("name", "Onbekend")
    title = conv.get("title", "")

    author_posts = [p for p in chroma_posts if p.get("author_profile_url") == profile_url]
    classification = author_posts[0].get("classification", "unknown") if author_posts else "unknown"
    about_text = author_posts[0].get("about", "") if author_posts else ""

    lines = [
        "## LinkedIn Conversatie Context",
        "",
        f"**Persoon:** {name} — {title}",
        f"**Profiel:** {profile_url}",
        f"**Classificatie:** {classification}",
    ]
    if about_text:
        lines.append(f"**About:** {about_text}")
    lines.append("")

    if author_posts:
        lines.append("**Posts van deze persoon in mijn database:**")
        for p in sorted(author_posts, key=lambda x: x.get("timestamp", ""))[:5]:
            date = str(p.get("timestamp", ""))[:10]
            excerpt = str(p.get("text", ""))[:100].replace("\n", " ")
            url = p.get("url", "")
            lines.append(f'- {date}: "{excerpt}..." — {url}')
        lines.append("")

    messages = conv.get("messages", [])
    if messages:
        lines.append("**Berichten die ik stuurde:**")
        for i, m in enumerate(sorted(messages, key=lambda x: x.get("date", "")), 1):
            mtype = m.get("type", "?")
            date = m.get("date", "?")
            post_url = m.get("post_url", "")
            content = m.get("content", "")
            if post_url:
                lines.append(f"{i}. {date} [{mtype}] op {post_url}:")
            else:
                lines.append(f"{i}. {date} [{mtype}]:")
            lines.append(f'   "{content}"')
        lines.append("")

    lines += [
        "**Jouw niche context:**",
        "OT engineer, 15 jaar fabrieken, bouwt Industrial Data Platform cursus.",
        "Open source stack: Mosquitto MQTT, N8N, FastAPI, MongoDB, Grafana, Docker.",
        'Flagship: "AVEVA Connect (€40K/jaar) vervangen met VPS van €8/maand."',
        "Ideale klant: PLC/SCADA engineer bij system integrator die wil groeien naar data architect.",
        "",
        "**Advies gevraagd:**",
        "Geef 2-3 opties voor mijn volgend bericht. Geen pitch, geen verkoop.",
        "Toon authentieke technische interesse. Bouw autoriteit in Industry 4.0 / IIoT / UNS.",
    ]

    return "\n".join(lines)
