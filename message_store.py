from __future__ import annotations
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

MESSAGES_DIR = Path(__file__).parent / "messages"


def get_slug(profile_url: str) -> str:
    """Extract slug from LinkedIn profile URL.
    'https://linkedin.com/in/metin-capraz-7b65364a/' -> 'metin-capraz-7b65364a'
    """
    match = re.search(r'/in/([^/?]+)', profile_url)
    if match:
        return match.group(1).rstrip("/")
    return re.sub(r'[^\w-]', '-', profile_url)[-50:]


def _conversation_path(profile_url: str) -> Path:
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    return MESSAGES_DIR / f"{get_slug(profile_url)}.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file to avoid corruption on interrupted writes."""
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False,
                                     suffix=".tmp", encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


def load_conversation(profile_url: str) -> dict:
    """Load conversation JSON or return empty template if not found or corrupt."""
    path = _conversation_path(profile_url)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"profile_url": profile_url, "name": "", "title": "", "messages": []}


def save_message(profile_url: str, name: str, title: str, message: dict) -> None:
    """Append a message to the conversation. Creates file if not exists."""
    conv = load_conversation(profile_url)
    conv["name"] = name or conv["name"]
    conv["title"] = title or conv["title"]
    conv["profile_url"] = profile_url
    if "id" not in message or not message["id"]:
        message["id"] = datetime.now().isoformat()
    conv["messages"].append(message)
    path = _conversation_path(profile_url)
    _atomic_write(path, conv)


def list_conversations() -> list[dict]:
    """List all conversations sorted by most recent message date (newest first)."""
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    conversations = []
    for path in MESSAGES_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                conv = json.load(f)
            dates = [m.get("date", "") for m in conv.get("messages", [])]
            conv["_last_date"] = max(dates) if dates else ""
            conversations.append(conv)
        except Exception:
            continue
    return sorted(conversations, key=lambda c: c["_last_date"], reverse=True)


def delete_message(profile_url: str, message_id: str) -> None:
    """Remove a message by ID. No-op if not found."""
    conv = load_conversation(profile_url)
    conv["messages"] = [m for m in conv["messages"] if m.get("id") != message_id]
    path = _conversation_path(profile_url)
    _atomic_write(path, conv)


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
