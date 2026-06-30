# TDgpt anomaly-detection op `idp/plc01/temperature`

> Toont het Solve-bewijsstuk: raw sensor-data → AI-detectie → een beslissing,
> volledig op de **gratis** TDengine-stack. Geen taosX, geen Enterprise.
> De data komt binnen via [`bridge.py`](bridge.py) (super-table `telemetry`,
> sub-table per topic, kolom `value` = DOUBLE).

Twee niveaus: een baseline-regel die **direct** werkt, en de echte TDgpt-AI die
de normaalband zélf leert.

---

## A. Baseline (vaste grenzen) — werkt meteen, geen AI

Per de IDP-canon hoort `idp/plc01/temperature` tussen 55–75 °C te liggen. Een
domweg-deterministische range-check, draait op de kale tdengine-image:

```sql
-- afwijkingen laatste uur t.o.v. een vaste verwachte band
SELECT _ts, value
FROM idp.telemetry
WHERE topic = 'idp/plc01/temperature'
  AND _ts > now - 1h
  AND (value > 75 OR value < 55)
ORDER BY _ts DESC
LIMIT 20;
```

```bash
docker exec -it tdengine taos -s "$(cat <<'SQL'
SELECT _ts, value FROM idp.telemetry
WHERE topic='idp/plc01/temperature' AND _ts > now-1h
  AND (value > 75 OR value < 55) ORDER BY _ts DESC LIMIT 20;
SQL
)"
```

Het probleem met deze aanpak: jíj moet de grenzen weten en handmatig
onderhouden. Verandert het setpoint, dan klopt de regel niet meer. Dat is precies
wat TDgpt oplost.

---

## B. TDgpt — de AI leert de band zelf (gratis, AGPL)

TDgpt-algoritmes (IQR, k-sigma, Grubbs, LOF) draaien op een **analysis node**
(`taosanode`). Die zit **niet** in de standaard `tdengine/tdengine` image — je
installeert hem apart (los, gratis component) en registreert hem één keer.

### B1. Eenmalige setup — anode starten + registreren

`taosanode` luistert op poort **6035** en wordt los geïnstalleerd (Python +
uWSGI service `taosanoded`, config `/etc/taos/taosanode.ini`). Installeer hem in
de tdengine-container of op dezelfde host, en registreer:

```sql
-- éénmalig, vanuit taos CLI
CREATE ANODE '127.0.0.1:6035';
SHOW ANODES;            -- check dat hij 'ready' is
```

> Install-pointer: TDengine docs → *TDgpt → Manage Analysis Nodes*. De anode is
> de gratis open-source AI-laag; alleen de model-evaluation tool + model-manager
> (Merlion/Kats) zijn Enterprise — die heb je hier niet nodig.

### B2. Anomaly detection — k-sigma (k=2)

`ANOMALY_WINDOW` is een pseudo-window dat opeenvolgende afwijkende punten
groepeert. De AI bepaalt zelf wat "afwijkend" is op basis van de data:

```sql
SELECT _wstart, _wend, COUNT(*) AS n, AVG(value) AS avg_c
FROM idp.telemetry
WHERE topic = 'idp/plc01/temperature'
  AND _ts > now - 6h
ANOMALY_WINDOW(value, "algo=ksigma,k=2");
```

```bash
docker exec -it tdengine taos -s "$(cat <<'SQL'
SELECT _wstart, _wend, COUNT(*) n, AVG(value) avg_c
FROM idp.telemetry
WHERE topic='idp/plc01/temperature' AND _ts > now-6h
ANOMALY_WINDOW(value, "algo=ksigma,k=2");
SQL
)"
```

Algoritme-opties (allemaal gratis): `algo=iqr` (default), `ksigma,k=N`,
`grubbs`, `lof`. Witte-ruis-check uit met `wncheck=0`.

### B3. Forecasting — voorspel de volgende punten

```sql
SELECT _wstart, FORECAST(value, "algo=holtwinters") AS verwacht
FROM idp.telemetry
WHERE topic = 'idp/plc01/temperature'
  AND _ts > now - 6h
INTERVAL(10s);
```

---

## Waarom dit het verhaal maakt

| | Baseline-regel (A) | TDgpt (B) |
|---|---|---|
| Wie kent de "normale" band? | jij, handmatig | de AI, geleerd uit data |
| Onderhoud bij setpoint-wijziging | breekt | past zich aan |
| Kosten | gratis | **gratis** (AGPL, geen Enterprise) |
| Waar draait het | in `taosd` | in `taosanode` (gratis component) |

De **Solve-test**: van `idp/plc01/temperature` → TDgpt detecteert een afwijking →
onderhouds-actie. Volledig open source op de €8/mnd-stack. Wat Enterprise kost is
niet de AI, maar de MLOps eromheen (welk model is het best + lifecycle-beheer) —
en dat heb je pas nodig bij tientallen modellen in productie.

Volledige afweging: [`research/idp/2026-06-30-tdengine-vs-monstermq-store-layer-ai-data-layer.md`](../../strategy-os/user-workspace/research/idp/2026-06-30-tdengine-vs-monstermq-store-layer-ai-data-layer.md)
