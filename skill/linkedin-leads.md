---
name: linkedin-leads
description: Analyseer de dagelijkse LinkedIn feed data, genereer reply-suggesties voor ideal clients en influencers, en schrijf het markdown rapport.
---

# LinkedIn Leads Skill

## Wat je doet

1. Bepaal de datum van vandaag (formaat: YYYY-MM-DD)
2. Lees het JSON sidecar bestand: `c:/tools/Basecamp-Compass/user-workspace/linkedin-feed/{datum}-raw.json`
3. Analyseer elke post per classificatie
4. Genereer voor elke relevante post 2-3 reply-opties
5. Schrijf het volledige rapport naar: `c:/tools/Basecamp-Compass/user-workspace/linkedin-feed/{datum}.md`
6. Bevestig: "Rapport klaar — open user-workspace/linkedin-feed/{datum}.md"

## Reply-filosofie (geen pitch)

- **Ideal clients:** Stel een technische vraag die laat zien dat jij het probleem beter snapt. Gebruik hun eigen keywords. Toon herkenning van de pijn.
- **Influencers:** Voeg iets toe wat hun volgers nog niet zeiden. Open source angle, OT-praktijkervaring, concrete tool-vergelijking.
- **Collega's/chef:** Warme professionele reactie. Geen strategie.
- **NOOIT:** "Ik heb een cursus / product / dienst..." of iets wat ruikt naar sales.

## Jouw niche context (gebruik dit bij het schrijven van replies)

Johannes is OT engineer, 15 jaar fabrieken (Siemens PLC/SCADA, historians, DCS). Bouwt de Industrial Data Platform cursus voor PLC/SCADA engineers bij system integrators. Open source stack: Mosquitto MQTT, N8N, FastAPI, MongoDB, Grafana, Docker. Flagship boodschap: "Ik vervang AVEVA Connect (€40K/jaar) met een VPS van €8/maand."

Ideale klant (Marco): PLC/SCADA engineer bij een system integrator, 35-42 jaar, wil groeien naar Industrial Data Architect.

## Rapport formaat

Gebruik de structuur uit de raw JSON (date, posts array, connections dict) om een rapport te genereren conform dit formaat:

```
# LinkedIn Intel — {datum}

## Vandaag: X ideal clients · Y influencers · Z collega's · N posts total

---

## PRIORITEIT 1 — Ideal Clients

### {naam} — {titel} @ {bedrijf}
**Post:** "{excerpt}"
**URL:** {url}
**Waarom relevant:** {keywords_matched}
**Connectie sinds:** {first_seen} | Posts gezien: {post_count}x

**Reply-opties:**
1. [herkenning + technische vraag]
2. [technische add-on, open source angle]
3. [nieuwsgierige vraag over hun specifieke situatie]

---

## PRIORITEIT 2 — Influencers

...

## OVERZICHT LEADS — Cumulatief

| Naam | Classificatie | Bedrijf | Posts gezien | Laatste activiteit |
```
