"""Generate personalized FB community invite messages for all target connections.

Outputs:
  - fb-community-outreach.md   (human-readable, copy-paste per person)
  - fb-community-queue.json    (machine-readable, used by --send-dms automator)
"""
from __future__ import annotations
import json
import sys
import io
import pathlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, "scraper")
sys.path.insert(0, ".")
from store import LinkedInStore
import message_store as ms

FB_LINK = "https://www.facebook.com/groups/1282445730514883/"
OUT_MD   = "c:/tools/Basecamp-Compass/user-workspace/linkedin-feed/fb-community-outreach.md"
OUT_JSON = "c:/tools/Basecamp-Compass/user-workspace/linkedin-feed/fb-community-queue.json"

TARGET_KEYWORDS = [
    "plc", "scada", "automation", "ot engineer", "ot specialist",
    "electrical engineer", "control engineer", "instrumentation",
    "dcs", "mes", "iiot", "industry 4.0", "system integrator",
    "process engineer", "commissioning", "siemens", "rockwell",
    "allen-bradley", "abb", "schneider", "honeywell", "yokogawa",
    "emerson", "aveva", "ignition", "historian", "field engineer",
]


def first_name(name: str) -> str:
    return name.strip().split()[0]


def make_hook(title: str, about: str) -> str:
    """Pick a role-specific opening hook. About text takes priority over title."""
    combined = (title + " " + about).lower()

    # About-specific signals (richer personalization when about is available)
    if about:
        ab = about.lower()
        if "niagara" in ab or "bms" in ab or "building automation" in ab:
            return "Working with building automation systems like Niagara, you know how much data sits inside these platforms — connecting it to a unified view is where the real value is."
        if "renewable" in ab or "solar" in ab or "photovoltaic" in ab:
            return "Renewable energy systems generate enormous amounts of data — and getting that connected to a proper data platform is still a challenge most sites haven't solved."
        if "tia portal" in ab or "step 7" in ab:
            return "As a Siemens TIA Portal engineer, you're programming the controllers that run the plant — and the data inside them deserves better than staying locked in the PLC."
        if "delta v" in ab or "deltav" in ab:
            return "DeltaV is powerful, but getting process data out to where analysts and engineers can actually use it is a whole separate challenge."
        if "pi system" in ab or "osisoft" in ab or "osisof" in ab:
            return "PI System is the gold standard for historian data — but at €40k+/year for a full stack, there's real demand for an open source alternative."

    # Title/general signals
    if "aveva" in combined:
        return "Working with AVEVA, you know the power of a connected data platform — and the price tag that comes with it."
    if "ignition" in combined:
        return "With Ignition experience you already know what a solid data backbone looks like — this community is about building that with open source tools."
    if "historian" in combined:
        return "As a historian specialist you know exactly how valuable process data is — and how locked-in it gets inside proprietary systems."
    if "mes" in combined:
        return "MES sits right at the intersection of shop floor and enterprise — the data integration challenge is real, and vendor platforms make it expensive."
    if "dcs" in combined:
        return "Working with DCS, you know how much valuable process data stays locked inside the system — connecting it downstream is where it gets interesting."
    if "iiot" in combined or "industry 4.0" in combined:
        return "Building IIoT solutions, you're probably wrestling with the same question: how do you connect everything without buying yet another expensive platform?"
    if "system integrat" in combined:
        return "As a system integrator your clients are asking more and more for data integration — and vendor platforms can eat the whole project budget."
    if "scada" in combined:
        return "As a SCADA engineer you're sitting on a goldmine of process data — getting it connected to modern dashboards and analytics is where it gets powerful."
    if "plc" in combined:
        return "As a PLC engineer you know how hard it is to get data out of controllers and into a place where you can actually use it."
    if "electrical engineer" in combined or "electrical & " in combined or "electrical e" in combined:
        return "Electrical engineers often know the plant data best — and are the ones figuring out how to get it to IT in a way that actually works."
    if "commissioning" in combined:
        return "As a commissioning engineer you've seen more control systems than most — and probably noticed how few of them actually talk to each other."
    if "siemens" in combined:
        return "Working in the Siemens ecosystem you're used to powerful tools — but vendor lock-in and licensing costs are a real challenge at smaller sites."
    if "abb" in combined:
        return "Working with ABB systems, you know how powerful they are — and how hard it can be to get data out to where your team can actually use it."
    if "field service" in combined or "field engineer" in combined:
        return "Field engineers see the real state of OT infrastructure every day — usually a patchwork of systems with no way to see the full picture."
    if "control" in combined:
        return "Control engineers produce more data than almost anyone in the plant — making it accessible is the next frontier."
    if "instrumentation" in combined:
        return "Instrumentation engineers are the source of the data — getting that connected and visible to the rest of the organisation is the challenge."
    if "process engineer" in combined:
        return "Process engineers need real-time and historical data to do their best work — and it's too often locked in systems they can't easily access."
    if "automation" in combined:
        return "Automation engineers are building the future of industrial sites — and a unified data platform is what ties it all together."
    return "As an OT/industrial professional, you know how fragmented plant data can be — every system in its own silo."


def make_message(t: dict) -> str:
    name = first_name(t["name"])
    hook = make_hook(t["title"], t["about"])
    return (
        f"Hey {name}\U0001f44b\n\n"
        f"{hook}\n\n"
        f"I just started a free community for OT/automation engineers \u2014 thought you might find it useful.\n\n"
        f"It\u2019s about building a unified Industrial Data Platform with open source tools (MQTT, MongoDB, Grafana, SQL) "
        f"instead of paying \u20ac30k+/year for vendor platforms like AVEVA.\n\n"
        f"\U0001f517 {FB_LINK}\n\n"
        f"No spam, no pitch \u2014 just practical knowledge from the field. Free to join.\n\n"
        f"Would love to see you there!\n\n"
        f"\u2014 Johannes"
    )


def build_section(header: str, items: list[dict]) -> list[str]:
    lines = [f"## {header} ({len(items)})", ""]
    for i, t in enumerate(items, 1):
        msg = make_message(t)
        about_snippet = t["about"][:120].replace("\n", " ") if t["about"] else ""
        lines.append(f"### {i}. {t['name']}")
        lines.append(f"**Functie:** {t['title']}")
        lines.append(f"**Profiel:** {t['url']}")
        if about_snippet:
            lines.append(f"**Info:** _{about_snippet}..._")
        lines.append("")
        lines.append("**Bericht:**")
        lines.append("")
        lines.append("```")
        lines.append(msg)
        lines.append("```")
        lines.append("")
    return lines


# ── Load data ──────────────────────────────────────────────────────────────────

store = LinkedInStore("c:/tools/linkedin-intel/db/chroma")
conns = store.get_all_connections()
convs = ms.list_conversations()
contacted = {c.get("profile_url", "") for c in convs if c.get("messages")}

targets: list[dict] = []
for c in conns:
    url = c.get("profile_url", "")
    if url in contacted:
        continue
    title_raw = c.get("title", "") or ""
    title_low = title_raw.lower()
    name = (c.get("name", "") or "").strip()
    if not name or not title_raw:
        continue
    match = next((kw for kw in TARGET_KEYWORDS if kw in title_low), None)
    if match:
        targets.append({
            "name": name,
            "title": title_raw,
            "url": url,
            "cls": c.get("classification", "unknown"),
            "about": c.get("about", "") or "",
            "kw": match,
        })


def sort_key(t: dict):
    rank = 0 if t["cls"] == "ideal_client" else 1 if t["cls"] == "influencer" else 2
    return (rank, t["name"])


targets.sort(key=sort_key)

ideal      = [t for t in targets if t["cls"] == "ideal_client"]
influencer = [t for t in targets if t["cls"] == "influencer"]
rest       = [t for t in targets if t["cls"] not in ("ideal_client", "influencer")]

# ── Write Markdown report ──────────────────────────────────────────────────────

lines: list[str] = [
    "# FB Community Outreach — Gepersonaliseerde berichten",
    "",
    f"**Totaal: {len(targets)} connecties** — gesorteerd op prioriteit  ",
    f"**Community:** {FB_LINK}",
    "",
    "> Stap 1: Bewerk `fb-community-queue.json` — zet `\"selected\": false` voor mensen die je wilt overslaan.  ",
    "> Stap 2: `python scraper/run.py --send-dms` — Playwright stuurt automatisch de geselecteerde DMs.",
    "",
    "---",
    "",
]

lines += build_section("\U0001f3af Prioriteit 1 \u2014 Ideal Clients", ideal)
lines += build_section("\u2b50 Prioriteit 2 \u2014 Influencers", influencer)
lines += build_section("\U0001f465 Prioriteit 3 \u2014 Overige Automation Engineers", rest)

pathlib.Path(OUT_MD).parent.mkdir(parents=True, exist_ok=True)
with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

# ── Write queue JSON for DM automator ─────────────────────────────────────────

# Load existing queue to preserve sent status
existing_queue: list[dict] = []
if pathlib.Path(OUT_JSON).exists():
    try:
        with open(OUT_JSON, encoding="utf-8") as f:
            existing_queue = json.load(f)
    except Exception:
        pass

existing_sent = {
    item["profile_url"]: item
    for item in existing_queue
}

queue: list[dict] = []
for t in targets:
    existing = existing_sent.get(t["url"], {})
    queue.append({
        "profile_url": t["url"],
        "name": t["name"],
        "title": t["title"],
        "classification": t["cls"],
        "about": t["about"],
        "message": make_message(t),
        # Preserve sent status from previous run; default selected=true, sent=false
        "selected": existing.get("selected", True),
        "sent": existing.get("sent", False),
        "sent_at": existing.get("sent_at", None),
    })

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(queue, f, ensure_ascii=False, indent=2)

enriched_count = sum(1 for t in targets if t["about"])
print(f"Saved {len(targets)} berichten naar:")
print(f"  MD:   {OUT_MD}")
print(f"  JSON: {OUT_JSON}")
print(f"  Ideal clients:  {len(ideal)}")
print(f"  Influencers:    {len(influencer)}")
print(f"  Overige:        {len(rest)}")
print(f"  Met About/Info: {enriched_count}/{len(targets)}")
