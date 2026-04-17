# LinkedIn Intel — CLAUDE.md

## Wat is dit?
LinkedIn scraper + dashboard voor OT/automation engineer outreach.
Scrapt posts, connecties en DM-berichten → slaat op in ChromaDB (vector DB) → dashboard via FastAPI + HTML.

## Stack
- **Scraper**: Playwright (Python) — `scraper/linkedin_scraper.py`
- **Store**: ChromaDB (lokaal, persistent) — `scraper/store.py`
- **API**: FastAPI — `api.py` (port 8000)
- **Dashboard**: Vanilla HTML/JS — `dashboard.html`
- **Message store**: ChromaDB via `message_store.py`

## Starten
```bash
# API starten
python api.py

# Dashboard: open dashboard.html in browser

# Scraper draaien
python scraper/run.py
```

## Scraper gedrag (run.py)
- Max **3 keywords** per run (detectie-reductie)
- Max **8 posts** per keyword
- Slaat alleen **nieuwe** posts/connecties op (check ChromaDB eerst)
- **About enrichment** alleen voor nieuwe connecties (direct na scrape)
- **DM inbox** automatisch bijgewerkt (laatste 2 dagen) aan einde van run
- Engagement scraping: uitgeschakeld

## ChromaDB Collections
| Collection | Inhoud |
|---|---|
| `posts` | LinkedIn posts met classificatie, keywords, reply_drafted flag |
| `connections` | Connecties met naam, titel, bedrijf, classificatie, about |
| `messages` | DM-berichten + comments, gededupliceerd per profiel |

## Deduplicatie (store.py)
- Posts: dedup op URL
- Connecties: dedup op profile_url (trailing slash genormaliseerd)
- Berichten: dedup op `sha256(profile_url|date|direction|content[:80])` — whitespace genormaliseerd
- Belt-and-suspenders: directe chroma_id check vóór elke `.add()`

## Semantische zoekopdrachten
ChromaDB gebruikt embeddings (`all-MiniLM-L6-v2`) — semantisch zoeken werkt via:
- Dashboard zoekbalk: naam → directe filter, geen naam → semantische zoekopdracht
- Preset knoppen: Positief, Cursus, Freelancer, Grafana/MQTT, Wil demo
- API: `GET /api/search?q=...&n=15`

## API endpoints
| Endpoint | Methode | Beschrijving |
|---|---|---|
| `/api/summary` | GET | Totalen: posts, connecties, gesprekken |
| `/api/posts` | GET | Alle posts, filterbaar op classificatie/search |
| `/api/connections` | GET | Connecties gesorteerd op prioriteit |
| `/api/conversations` | GET | Alle gesprekken |
| `/api/conversations/{slug}` | GET | Gesprek van één contact |
| `/api/conversations/{slug}/messages` | POST | Bericht toevoegen |
| `/api/conversations/{slug}/messages/{id}` | DELETE | Bericht verwijderen |
| `/api/search` | GET | Semantisch zoeken in berichten |
| `/api/context/{slug}` | GET | Clipboard context voor Claude Code |
| `/api/posts/reply` | PATCH | Reply drafted markeren |
| `/api/clipboard` | POST | Tekst naar Windows clipboard |

## Classificaties
- `ideal_client` — matcht op IDEAL_CLIENT_TITLES
- `influencer` — matcht op INFLUENCER_KEYWORDS
- `colleague` — matcht op COLLEAGUE_NAMES
- `neutral` — geen match

## Environment (.env in scraper/)
```
CHROMA_PATH=c:/tools/linkedin-intel/db/chroma
REPORT_OUTPUT=c:/tools/Basecamp-Compass/user-workspace/linkedin-feed
KEYWORDS=MQTT,OPC-UA,OPC UA,SCADA,PLC,historian,IIoT,Industry 4.0
INFLUENCER_KEYWORDS=...
IDEAL_CLIENT_TITLES=...
COLLEAGUE_NAMES=...
```

## Bekende valkuilen
- LinkedIn detectie: houd KW_LIMIT laag (3), POSTS_PER_KW max 8
- Profile URL normalisatie: altijd `.split("?")[0].rstrip("/")` gebruiken
- DM datum fallback: was `datetime.now()` → nu `""` (anders duplicaten bij herhaalde runs)
- `context.new_page()` buiten try/except → TargetClosedError propageren in `enrich_connections`
