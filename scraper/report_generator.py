from __future__ import annotations
from datetime import datetime


def generate_report(
    date: str,
    posts: list[dict],
    connections: dict[str, dict],
    replies: dict[str, list[str]],
) -> str:
    ideal = [p for p in posts if p["classification"] == "ideal_client"]
    influencers = [p for p in posts if p["classification"] == "influencer"]
    colleagues = [p for p in posts if p["classification"] == "colleague"]

    lines = [
        f"# LinkedIn Intel — {date}",
        "",
        f"## Vandaag: {len(ideal)} ideal client{'s' if len(ideal) != 1 else ''} · "
        f"{len(influencers)} influencer{'s' if len(influencers) != 1 else ''} · "
        f"{len(colleagues)} collega{'s' if len(colleagues) != 1 else ''} · "
        f"{len(posts)} posts total",
        "",
        "---",
        "",
    ]

    if ideal:
        lines += ["## PRIORITEIT 1 — Ideal Clients (reageren vandaag)", ""]
        for post in ideal:
            conn = connections.get(post["author_profile_url"], {})
            post_replies = replies.get(post["url"], [])
            lines += _format_post_block(post, conn, post_replies)

    if influencers:
        lines += ["## PRIORITEIT 2 — Influencers (zichtbaarheid opbouwen)", ""]
        for post in influencers:
            conn = connections.get(post["author_profile_url"], {})
            post_replies = replies.get(post["url"], [])
            lines += _format_post_block(post, conn, post_replies)

    if colleagues:
        lines += ["## COLLEGA'S — Gezien vandaag", ""]
        for post in colleagues:
            excerpt = post["text"][:120] + ("..." if len(post["text"]) > 120 else "")
            lines += [
                f"### {post['author_name']}",
                f"**Post:** \"{excerpt}\"",
                f"**URL:** {post['url']}",
                "",
            ]

    lines += [
        "---",
        "",
        "## OVERZICHT LEADS — Cumulatief",
        "",
        "| Naam | Classificatie | Bedrijf | Posts gezien | Laatste activiteit |",
        "|------|--------------|---------|--------------|-------------------|",
    ]
    for profile_url, conn in connections.items():
        lines.append(
            f"| {conn.get('name', '?')} | {conn.get('classification', '?')} | "
            f"{conn.get('company', '?')} | {conn.get('post_count', 0)} | "
            f"{conn.get('last_seen', conn.get('first_seen', '?'))} |"
        )

    lines += ["", "---", f"*Gegenereerd op {datetime.now().strftime('%Y-%m-%d %H:%M')}*", ""]
    return "\n".join(lines)


def _format_post_block(post: dict, conn: dict, post_replies: list[str]) -> list[str]:
    excerpt = post["text"][:200] + ("..." if len(post["text"]) > 200 else "")
    title = conn.get("title", "onbekend")
    company = conn.get("company", "onbekend")
    first_seen = conn.get("first_seen", "?")
    post_count = conn.get("post_count", 0)
    keywords = ", ".join(post.get("keywords_matched", []))

    lines = [
        f"### {post['author_name']} — {title} @ {company}",
        f"**Post:** \"{excerpt}\"",
        f"**URL:** {post['url']}",
        f"**Waarom relevant:** {keywords}",
        f"**Connectie sinds:** {first_seen} | Posts gezien: {post_count}x",
        "",
    ]

    if post_replies:
        lines.append("**Reply-opties (kies er één, pas aan in je eigen stijl):**")
        for i, reply in enumerate(post_replies, 1):
            lines.append(f"{i}. {reply}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines
