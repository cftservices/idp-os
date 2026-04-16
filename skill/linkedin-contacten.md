---
name: linkedin-contacten
description: Analyseer wie je nog niet benaderd hebt of te lang geleden, genereer kant-en-klare concept berichten per persoon.
---

# LinkedIn Contacten Skill

## Wat je doet

1. Laad alle connecties + posts uit ChromaDB
2. Laad alle berichtenhistories uit `messages/` directory
3. Kruis: wie is `ideal_client` of `influencer` maar heeft GEEN bericht?
4. Kruis: wie heeft een bericht maar is > 14 dagen geleden voor het laatste benaderd?
5. Genereer per persoon een kant-en-klaar concept bericht
6. Output als markdown rapport — kopieer en plak

## Data inladen

Voer deze Python code uit in de terminal (vanuit `c:/tools/linkedin-intel`):

```python
import sys, json
from pathlib import Path
sys.path.insert(0, "c:/tools/linkedin-intel")
sys.path.insert(0, "c:/tools/linkedin-intel/scraper")
from store import LinkedInStore
import message_store as ms
from datetime import datetime, timedelta

store = LinkedInStore("c:/tools/linkedin-intel/db/chroma")
connections = store.get_all_connections()
all_posts = store.get_all_posts()
conversations = ms.list_conversations()

# Build lookup maps
msg_map = {c.get("profile_url", ""): c for c in conversations}
post_map: dict[str, list] = {}
for p in all_posts:
    url = p.get("author_profile_url", "")
    post_map.setdefault(url, []).append(p)

today = datetime.now().date()
cutoff_14d = (today - timedelta(days=14)).isoformat()

direct_actie = []   # ideal_client/influencer, nooit benaderd
follow_up = []      # benaderd maar > 14 dagen geleden
geen_actie = []     # recent contact

for conn in connections:
    cls = conn.get("classification", "unknown")
    if cls not in ("ideal_client", "influencer"):
        continue
    url = conn.get("profile_url", "")
    conv = msg_map.get(url)
    messages = conv.get("messages", []) if conv else []
    
    if not messages:
        direct_actie.append(conn)
    else:
        last_date = max(m.get("date", "") for m in messages)
        if last_date < cutoff_14d:
            conn["_last_contact"] = last_date
            follow_up.append(conn)
        else:
            geen_actie.append(conn)

print(json.dumps({
    "direct_actie": direct_actie,
    "follow_up": follow_up,
    "geen_actie": geen_actie,
    "post_map": {k: v[:3] for k, v in post_map.items()},
}, default=str, indent=2))
```

## Rapport genereren

Na het uitvoeren van de code hierboven, genereer het volgende rapport op basis van de JSON output:

```
# LinkedIn Contacten Analyse — {datum}

## Samenvatting
- **Direct actie (nooit benaderd):** N personen
- **Follow-up (> 14 dagen):** N personen
- **Geen actie nodig:** N personen

---

## Direct Actie — Nooit Benaderd

### 1. [Naam] — [Titel]
**Profiel:** [url]
**Classificatie:** ideal_client | influencer
**Posts gezien:** N × | **About:** "[eerste 100 tekens van about]"

**Concept bericht:**
> [Specifiek bericht gebaseerd op hun About + recente post. Geen pitch. Technische vraag of herkenning van hun pijn. Max 3 zinnen.]

---

## Follow-up — Laatste contact > 14 dagen geleden

### 1. [Naam] — [Titel]
**Profiel:** [url]
**Laatste contact:** [datum]
**Posts gezien:** N ×

**Concept follow-up:**
> [Korte, persoonlijke follow-up. Verwijs naar vorig bericht of nieuwe post van hen.]

---

## Geen Actie Nodig — Recent Contact

| Naam | Classificatie | Laatste contact |
|------|--------------|-----------------|
| ... | ... | ... |
```

## Reply-filosofie (geen pitch)

- **Ideal clients:** Stel een technische vraag die laat zien dat jij het probleem beter snapt. Gebruik hun eigen keywords.
- **Influencers:** Voeg iets toe wat hun volgers nog niet zeiden. Open source angle, concrete tool.
- **NOOIT:** pitch, product push, "ik heb een cursus"

## Jouw niche context

OT engineer, 15 jaar fabrieken (Siemens PLC/SCADA, historians, DCS). Bouwt de Industrial Data Platform cursus voor PLC/SCADA engineers bij system integrators. Open source stack: Mosquitto MQTT, N8N, FastAPI, MongoDB, Grafana, Docker. Flagship: "AVEVA Connect (€40K/jaar) vervangen met VPS van €8/maand." Ideale klant: PLC/SCADA engineer bij system integrator, 35-42 jaar, wil groeien naar Industrial Data Architect.
