---
name: linkedin-reply
description: Genereer 3 reply-opties voor een specifieke LinkedIn post-URL op basis van ChromaDB data en berichtenhistorie.
---

# LinkedIn Reply Skill

## Gebruik

`/linkedin-reply <post-url>`

Vervang `<post-url>` met de volledige LinkedIn post URL.

## Wat je doet

1. Zoek de post op in ChromaDB via de opgegeven URL
2. Laad de berichtenhistorie van de auteur
3. Laad de About tekst van de auteur (uit ChromaDB connections)
4. Genereer 3 reply-opties
5. Output naar stdout — kopieer de beste optie

## Data inladen

Voer deze Python code uit (vervang de POST_URL waarde met de opgegeven URL):

```python
import sys, json
from pathlib import Path
sys.path.insert(0, "c:/tools/linkedin-intel")
sys.path.insert(0, "c:/tools/linkedin-intel/scraper")
from store import LinkedInStore
import message_store as ms

POST_URL = "VERVANG_DIT_MET_DE_POST_URL"

store = LinkedInStore("c:/tools/linkedin-intel/db/chroma")
all_posts = store.get_all_posts()

# Find the post
post = next((p for p in all_posts if p.get("url") == POST_URL), None)

if not post:
    print(f"Post niet gevonden in ChromaDB: {POST_URL}")
    print("Tip: zorg dat de post eerst gescraped is via python scraper/run.py")
else:
    author_url = post.get("author_profile_url", "")
    conn = store.get_connection(author_url) or {}
    conv = ms.load_conversation(author_url)
    
    print(json.dumps({
        "post": post,
        "author_connection": conn,
        "author_messages": conv.get("messages", []),
    }, default=str, indent=2))
```

## Reply genereren

Na het inladen van de data, genereer:

```
# Reply Suggesties — [Auteur Naam]

**Post:** "[eerste 150 tekens van post tekst]..."
**URL:** [post url]
**Auteur:** [naam] — [titel]
**Classificatie:** ideal_client | influencer
**About:** "[eerste 100 tekens]"
**Eerder contact:** [ja/nee — laatste bericht: datum indien ja]

---

## Optie 1 — Herkenning + Technische Vraag
> [Toon dat je het probleem herkent. Stel een specifieke technische vraag die laat zien dat jij dieper zit dan de gemiddelde reactie.]

## Optie 2 — Open Source / Praktijk Angle
> [Voeg iets toe wat anderen niet zeggen. Open source alternatief, concrete tool, praktijkervaring met dezelfde stack.]

## Optie 3 — Nieuwsgierige Vraag
> [Korte nieuwsgierige vraag over hun specifieke situatie of aanpak. Nodigt uit tot gesprek.]

---

**Advies:** [Welke optie het beste past en waarom, in 1 zin.]
```

## Reply-filosofie (geen pitch)

- **Ideal clients:** Stel een technische vraag die laat zien dat jij het probleem beter snapt.
- **Influencers:** Voeg iets toe wat hun volgers nog niet zeiden. Open source angle, OT-praktijkervaring.
- **Als je al eerder contact had:** Verwijs subtiel terug ("Zoals ik eerder vroeg over X...")
- **NOOIT:** "Ik heb een cursus / product / dienst..." of iets wat ruikt naar sales.

## Jouw niche context

OT engineer, 15 jaar fabrieken (Siemens PLC/SCADA, historians, DCS). Bouwt de Industrial Data Platform cursus voor PLC/SCADA engineers bij system integrators. Open source stack: Mosquitto MQTT, N8N, FastAPI, MongoDB, Grafana, Docker. Flagship: "AVEVA Connect (€40K/jaar) vervangen met VPS van €8/maand."
