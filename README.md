# LinkedIn Intel

LinkedIn scraper en dashboard voor gerichte outreach door OT/automation engineers.

Scrapt relevante posts en connecties op basis van keywords, slaat alles op in een lokale vector database (ChromaDB), en toont een overzichtelijk dashboard voor reply-tracking en DM-beheer.

## Wat het doet

- **Posts scrapen** op keywords (MQTT, OPC-UA, SCADA, PLC, IIoT, etc.)
- **Connecties bijhouden** met classificatie: ideal client, influencer, collega
- **DM-inbox bijwerken** — laatste 2 dagen automatisch gesynchroniseerd
- **Semantisch zoeken** in gesprekken via ChromaDB embeddings
- **Reply tracker** — bijhouden welke posts al bereikt zijn
- **Context bouwen** voor Claude Code reply-suggesties

## Stack

| Component | Technologie |
|---|---|
| Scraper | Python + Playwright |
| Database | ChromaDB (lokaal, vector DB) |
| Backend API | FastAPI |
| Dashboard | HTML + Vanilla JS |

## Installatie

```bash
# Dependencies installeren
pip install playwright chromadb fastapi uvicorn python-dotenv

# Playwright browsers
playwright install chromium

# .env aanmaken in scraper/
cp scraper/.env.example scraper/.env
# Vul KEYWORDS, IDEAL_CLIENT_TITLES, etc. in
```

## Gebruik

```bash
# 1. API starten
python api.py
# → http://localhost:8000

# 2. Dashboard openen
# Open dashboard.html in browser

# 3. Scraper draaien
python scraper/run.py
```

## Scraper logica

Elke run doet het volgende in volgorde:

1. **Posts scrapen** — max 3 keywords × 8 posts = max 24 posts per run
2. **Nieuwe connecties** — alleen connecties die nog niet in de DB staan (max 10 per run)
3. **About enrichment** — direct voor nieuwe connecties, geen aparte pass
4. **DM inbox** — laatste 2 dagen bijwerken, duplicaten worden geskipt

Bewust beperkt gehouden om LinkedIn detectie te minimaliseren.

## Dashboard

![Dashboard](dashboard-screenshot.png)

### Tabs
- **Overzicht** — metrics en grafiek per dag
- **Posts** — alle gescrapete posts, filterbaar en doorzoekbaar
- **Replies** — priority tracking: wie nog te bereiken, wie al gedaan
- **Contacten** — twee-kolom view met DM-geschiedenis en semantische zoekfunctie

### Semantisch zoeken

De zoekbalk in Contacten werkt op twee manieren:
- **Naam** → directe filter
- **Inhoud** → semantische zoekopdracht via ChromaDB (bijv. *"interested in learning more"*)

Preset knoppen: Positief gereageerd · Wil demo · Geïnteresseerd in cursus · Freelancer · Grafana/MQTT

## API

```
GET  /api/summary                          — totalen
GET  /api/posts?classification=ideal_client — posts filteren
GET  /api/connections                      — connecties gesorteerd op prioriteit
GET  /api/search?q=interested+in+MQTT     — semantisch zoeken
GET  /api/context/{slug}                   — clipboard context voor Claude Code
POST /api/conversations/{slug}/messages    — bericht loggen
```

Volledige documentatie: `http://localhost:8000/docs`

## Deduplicatie

- Posts: op URL
- Connecties: op genormaliseerde profile URL (trailing slash + query params gestript)
- Berichten: op `sha256(profile_url|date|direction|content[:80])` met whitespace normalisatie

## Environment variabelen

```env
CHROMA_PATH=c:/tools/linkedin-intel/db/chroma
REPORT_OUTPUT=c:/tools/Basecamp-Compass/user-workspace/linkedin-feed
KEYWORDS=MQTT,OPC-UA,SCADA,PLC,historian,IIoT,Industry 4.0
INFLUENCER_KEYWORDS=Walker Reynolds,Scott Leroy,...
IDEAL_CLIENT_TITLES=automation engineer,SCADA engineer,...
COLLEAGUE_NAMES=...
```

## Licentie

Privé gebruik — niet voor publieke distributie.
