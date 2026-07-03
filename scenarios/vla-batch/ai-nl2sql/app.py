"""vla-ai — "Praat met je fabriek" (lokale NL->SQL over de vla data-layer).

Volledig open-source + lokaal: een natuurlijke-taal vraag gaat naar een LOKAAL
Ollama-model (geen externe API, geen data naar buiten), dat SQL genereert voor de
TDengine-historian. De SQL wordt read-only uitgevoerd via taosAdapter REST en het
resultaat wordt (optioneel) door hetzelfde model tot een kort antwoord geformuleerd.

Dit is de open-source tegenhanger van IDMP's "AI Chat": de data-laag maakt AI
mogelijk zodra de data ISA-95-context heeft — niet andersom.

Env:
  OLLAMA_URL     http://vla-ollama:11434
  OLLAMA_MODEL   qwen2.5:3b
  TD_URL         http://vla-tdengine:6041
  TD_DB          idp
  TD_USER        root
  TD_PASS        (uit .env)
  PHRASE         1  -> tweede LLM-call formuleert een natuurlijk antwoord
"""
from __future__ import annotations

import os
import re

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://vla-ollama:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
TD_URL = os.environ.get("TD_URL", "http://vla-tdengine:6041").rstrip("/")
TD_DB = os.environ.get("TD_DB", "idp")
TD_USER = os.environ.get("TD_USER", "root")
TD_PASS = os.environ.get("TD_PASS", "taosdata")
PHRASE = os.environ.get("PHRASE", "1") not in ("0", "false", "False")

app = FastAPI(title="vla-ai — Praat met je fabriek", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── schema-context voor het model (few-shot + harde regels) ─────────────────
SCHEMA = """Je bent een TDengine SQL-assistent voor een chocolade-vla fabriek (DairyWorks).
Er is EEN database `idp` met EEN super-table `telemetry`:
  _ts       TIMESTAMP  (tijd van de meting)
  `value`   DOUBLE     (numerieke meetwaarde; NULL voor tekst-signalen)
  valuestr  VARCHAR    (tekst-waarde: batch-state, recept; NULL voor getallen)
  `topic`   TAG NCHAR  (UNS-topic, bv 'DairyWorks/Vla/Cook/cook-unit-01/Status/viscosity_cP')
  src       TAG NCHAR

Signaal-topics (DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}):
  Cook/cook-unit-01/Status/viscosity_cP   -> viscositeit (cP), spec 150-300, de kwaliteits-Solve
  Cook/cook-unit-01/Status/temp_C         -> kooktemperatuur
  Mixing/process-tank-01/Status/agitator_rpm -> roerder toerental
  Filling/filler-01/Status/packs_total    -> gevulde pakken
  Batch/Status/state                      -> batch-fase (tekst, in valuestr)
  Batch/Status/active_recipe              -> recept (tekst, in valuestr)

HARDE REGELS:
- `value` en `topic` zijn gereserveerde woorden: ALTIJD backticks eromheen.
- Tabel altijd als idp.`telemetry`.
- Alleen SELECT of SHOW. NOOIT INSERT/UPDATE/DELETE/DROP/ALTER/CREATE.
- Numerieke vragen: filter `value` IS NOT NULL.
- ANOMALY_WINDOW(`value`, "algo=iqr") gebruik je UITSLUITEND als de vraag
  expliciet over afwijkingen/anomalieen/uitschieters gaat. Bij gewone vragen
  (laatste, hoogste, laagste, gemiddelde, aantal, tel) gebruik je NOOIT
  ANOMALY_WINDOW — dan is het een simpele aggregatie (LAST/MAX/MIN/AVG/COUNT).
- Combineer ANOMALY_WINDOW NOOIT met MAX/MIN/LAST/COUNT in dezelfde query.
- Geef UITSLUITEND de SQL, 1 statement, geen uitleg, geen markdown.

Voorbeelden:
Vraag: wat is de laatste viscositeit?
SQL: SELECT LAST(`value`) FROM idp.`telemetry` WHERE `topic`='DairyWorks/Vla/Cook/cook-unit-01/Status/viscosity_cP'
Vraag: laat afwijkingen in de viscositeit zien
SQL: SELECT _wstart, _wend, COUNT(*), AVG(`value`) FROM idp.`telemetry` WHERE `topic` LIKE '%viscosity%' AND `value` IS NOT NULL ANOMALY_WINDOW(`value`, "algo=iqr")
Vraag: hoeveel metingen zijn er in totaal?
SQL: SELECT COUNT(*) FROM idp.`telemetry`
Vraag: wat is de hoogste kooktemperatuur?
SQL: SELECT MAX(`value`) FROM idp.`telemetry` WHERE `topic`='DairyWorks/Vla/Cook/cook-unit-01/Status/temp_C' AND `value` IS NOT NULL
"""

_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke)\b", re.I)
_FENCE = re.compile(r"```(?:sql)?|```", re.I)
# qwen2.5:3b plakt ANOMALY_WINDOW te graag op gewone aggregaties. Deterministische
# guard: alleen houden als de vraag echt over afwijkingen gaat.
_ANOM_KW = re.compile(r"anomal|afwijk|uitschiet|outlier|abnormaal|afwijkend|raar|vreemd", re.I)
_ANOM_CLAUSE = re.compile(r"\s*ANOMALY_WINDOW\s*\([^)]*\)", re.I)


class Ask(BaseModel):
    question: str


def _ollama(prompt: str, num_predict: int = 200) -> str:
    r = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
              "options": {"temperature": 0.0, "num_predict": num_predict}},
        timeout=120,
    )
    r.raise_for_status()
    return (r.json().get("response") or "").strip()


def _extract_sql(text: str) -> str:
    text = _FENCE.sub("", text).strip()
    # pak de eerste SELECT/SHOW t/m einde of eerste ';'
    m = re.search(r"(SELECT|SHOW)\b.*", text, re.I | re.S)
    sql = (m.group(0) if m else text).strip()
    sql = sql.split(";")[0].strip()
    return sql


def _guard(sql: str) -> None:
    low = sql.lstrip().lower()
    if not (low.startswith("select") or low.startswith("show")):
        raise HTTPException(400, f"alleen SELECT/SHOW toegestaan (kreeg: {sql[:60]})")
    if _WRITE.search(sql):
        raise HTTPException(400, "schrijf-statement geweigerd")


def _run_sql(sql: str) -> dict:
    r = requests.post(f"{TD_URL}/rest/sql/{TD_DB}", data=sql.encode("utf-8"),
                      auth=(TD_USER, TD_PASS), timeout=30)
    j = r.json()
    if j.get("code", 0) != 0:
        raise HTTPException(400, f"TDengine: {j.get('desc', 'query fout')}")
    cols = [c[0] for c in j.get("column_meta", [])]
    rows = j.get("data", [])
    return {"columns": cols, "rows": rows, "row_count": len(rows)}


_CHAT_HTML = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Praat met je fabriek — DairyWorks Vla</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;background:#0f1115;color:#e6e6e6}
 h1{font-size:1.3rem} .sub{color:#8a94a6;margin-top:-.6rem;font-size:.9rem}
 .ex{display:flex;flex-wrap:wrap;gap:.4rem;margin:.8rem 0}
 .ex button{background:#1b2130;color:#cbd5e1;border:1px solid #2a3446;border-radius:1rem;padding:.3rem .7rem;font-size:.82rem;cursor:pointer}
 form{display:flex;gap:.5rem;margin:1rem 0}
 input{flex:1;padding:.7rem;border-radius:.5rem;border:1px solid #2a3446;background:#151a23;color:#fff;font-size:1rem}
 button.go{background:#3b82f6;color:#fff;border:0;border-radius:.5rem;padding:0 1.2rem;font-weight:600;cursor:pointer}
 .a{background:#151a23;border:1px solid #2a3446;border-radius:.6rem;padding:1rem;margin:.6rem 0}
 .a .answer{font-size:1.05rem} .a code{color:#7dd3fc;font-size:.8rem;word-break:break-all}
 .a table{border-collapse:collapse;margin-top:.5rem;font-size:.82rem} .a td,.a th{border:1px solid #2a3446;padding:.2rem .5rem}
 .muted{color:#8a94a6;font-size:.8rem}
</style></head><body>
<h1>🏭 Praat met je fabriek</h1>
<p class="sub">Lokale AI (Ollama + TDengine) — vraag in gewone taal, geen data verlaat de VPS.</p>
<div class="ex">
 <button onclick="ask(this.textContent)">wat is de laatste viscositeit?</button>
 <button onclick="ask(this.textContent)">wat is de hoogste kooktemperatuur?</button>
 <button onclick="ask(this.textContent)">hoeveel metingen zijn er?</button>
 <button onclick="ask(this.textContent)">laat afwijkingen in de viscositeit zien</button>
</div>
<form onsubmit="ask(document.getElementById('q').value);return false">
 <input id="q" placeholder="Stel een vraag over de vla-productie..." autocomplete="off">
 <button class="go" type="submit">Vraag</button>
</form>
<div id="out"></div>
<script>
async function ask(q){
 if(!q)return; document.getElementById('q').value=q;
 const out=document.getElementById('out');
 const box=document.createElement('div'); box.className='a';
 box.innerHTML='<div class="muted">denkt na… (lokaal model, kan ~20s duren)</div>';
 out.prepend(box);
 try{
  const r=await fetch('api/v1/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
  const d=await r.json();
  if(!r.ok){box.innerHTML='<div class="answer">⚠ '+(d.detail||'fout')+'</div>';return;}
  let t=''; if(d.columns&&d.rows&&d.rows.length){t='<table><tr>'+d.columns.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+d.rows.slice(0,10).map(row=>'<tr>'+row.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</table>';}
  box.innerHTML='<div class="answer">'+(d.answer||'(geen antwoord)')+'</div>'+t+'<div class="muted" style="margin-top:.5rem">SQL: <code>'+d.sql+'</code></div>';
 }catch(e){box.innerHTML='<div class="answer">⚠ netwerkfout</div>';}
}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def chat_ui():
    return _CHAT_HTML


@app.get("/api/v1/health")
def health():
    ok = True
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).raise_for_status()
    except Exception:
        ok = False
    return {"status": "ok" if ok else "degraded", "model": OLLAMA_MODEL,
            "ollama": ok, "phrase": PHRASE}


@app.post("/api/v1/ask")
def ask(body: Ask):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "lege vraag")
    sql = _extract_sql(_ollama(f"{SCHEMA}\nVraag: {q}\nSQL:", num_predict=200))
    # strip een ongewenste ANOMALY_WINDOW als de vraag niet over afwijkingen gaat
    if _ANOM_CLAUSE.search(sql) and not _ANOM_KW.search(q):
        sql = _ANOM_CLAUSE.sub("", sql).strip()
    _guard(sql)
    result = _run_sql(sql)
    answer = None
    if result["row_count"] == 0:
        # Geen data -> NIET door het model laten verzinnen (dat hallucineert).
        answer = "Geen data gevonden voor die vraag."
    elif PHRASE:
        preview = {"columns": result["columns"], "rows": result["rows"][:20]}
        try:
            answer = _ollama(
                "Je bent een fabrieks-analist. Beantwoord de vraag in 1-2 korte "
                "Nederlandse zinnen, UITSLUITEND op basis van het query-resultaat "
                "hieronder. Verzin GEEN getallen of eenheden die er niet staan. "
                "Viscositeit is in cP, temperatuur in graden Celsius.\n"
                f"Vraag: {q}\nResultaat: {preview}\nAntwoord:",
                num_predict=160,
            )
        except Exception:
            answer = None
    return {"question": q, "sql": sql, **result, "answer": answer}
