# TDengine Store-laag PoC

> Bewijst dat je TDengine TSDB-OSS als **Store-laag** (stap 4) kunt gebruiken
> **zonder** de Enterprise-connectoren (taosX MQTT/OPC-UA). De brug is ~150
> regels open-source Python — precies het skills-bridge-werk dat het programma
> leert. Past binnen het €8/mnd, open-source, geen-lock-in proof point.

## Wat het is

```
MonsterMQ (broker, gratis OPC-UA/MQTT)
   |  subscribe idp/# + DairyPlant/# + bakery-works-utrecht/#
   v
bridge.py  (paho-mqtt -> InfluxDB line protocol)
   |  POST :6041/influxdb/v1/write   (schemaless, geen CREATE TABLE)
   v
TDengine TSDB-OSS   super-table `telemetry`, 1 sub-table per topic
   |
   v
Grafana (native tdengine-datasource, SQL)
```

MonsterMQ blijft de broker (zijn OPC-UA/MQTT zijn gratis). TDengine draait
**naast** MongoDB — je vervangt niets, je vergelijkt twee stores op dezelfde
live data.

## Voorwaarde

De hoofd-stack draait al:

```bash
docker compose -f docker-compose.v3.yml up -d
```

## Starten

```bash
# 1. netwerknaam van de draaiende stack verifieren
docker network ls | grep idp        # verwacht: idp-v3_idp-network

# 2. overlay starten (pas 'name:' in de compose aan als de netwerknaam afwijkt)
docker compose -f tdengine-poc/docker-compose.tdengine-poc.yml up -d --build

# 3. logs van de brug volgen
docker logs -f mqtt-tdengine-bridge   # "wrote N points" = het werkt
```

## Verifieren

```bash
# hoeveel datapunten?
docker exec -it tdengine taos -s "SELECT COUNT(*) FROM idp.telemetry"

# 1 sub-table per MQTT-topic (de TSDB-discipline)
docker exec -it tdengine taos -s "SHOW idp.tables"

# laatste waardes per topic
docker exec -it tdengine taos -s \
  "SELECT last_row(value), topic FROM idp.telemetry GROUP BY topic"

# tijd-aggregatie (waar TDengine MongoDB verslaat): 10s-gemiddelde, laatste uur
docker exec -it tdengine taos -s \
  "SELECT _wstart, AVG(value) FROM idp.telemetry \
   WHERE topic='idp/plc01/temperature' AND _ts >= now-1h INTERVAL(10s)"
```

## Grafana koppelen

TDengine heeft een officiele Grafana-datasource. Voeg aan de **grafana**-service
in `docker-compose.v3.yml` toe (env `GF_INSTALL_PLUGINS`):

```yaml
    environment:
      GF_INSTALL_PLUGINS: marcusolsson-json-datasource,tdengine-datasource
```

Herstart Grafana, voeg datasource **TDengine** toe:
- Host: `http://tdengine:6041`
- User / Password: `root` / `taosdata`

Query in een panel (TDengine SQL, niet PromQL):

```sql
SELECT _wstart AS ts, AVG(value) AS temp
FROM idp.telemetry
WHERE topic = 'idp/plc01/temperature' AND _ts >= now - 1h
INTERVAL(10s)
```

## AI-demo (Solve-bewijsstuk)

Anomaly-detection + forecasting op `idp/plc01/temperature`, volledig op de gratis
stack (TDgpt, AGPL — geen Enterprise): [`tdgpt-example.md`](tdgpt-example.md).
Bevat een baseline-regel die meteen werkt + de echte TDgpt-AI die de normaalband
zelf leert.

## Opruimen

```bash
docker compose -f tdengine-poc/docker-compose.tdengine-poc.yml down      # stack intact, data blijft
docker compose -f tdengine-poc/docker-compose.tdengine-poc.yml down -v   # ook TDengine-data wissen
```

## Wat dit bewust NIET gebruikt (= Enterprise / betaald)

| Feature | Waarom niet nodig in de PoC |
|---------|------------------------------|
| **taosX** (zero-code MQTT/OPC-UA in-ingestion) | vervangen door `bridge.py` (open source) |
| taos-explorer web-GUI | we gebruiken `taos` CLI + Grafana |
| tiered hot/cold storage, HA dual-replica, RBAC/audit/encryptie at-rest | niet nodig voor 1 VPS PoC |

De les: TDengine's **database** is gratis en sterk; de **OT-connectoren** zijn
de paywall — hetzelfde lock-in-patroon als AVEVA Connect. Deze brug stapt eromheen.

## Aandachtspunten

- **RAM:** TDengine wil ~1-2 GB comfortabel. Op een CX22 (4 GB) naast de v3-stack
  is het krap maar testbaar; voor een blijvende opstelling CX32 overwegen.
- **AGPL-3.0:** prima voor eigen/cursist-deploys. Let op bij een eventuele
  gehoste TechFlow-dienst (broncode-verplichting).
- **Non-numerieke payloads** (recept-namen, fase-strings, `…/All` JSON-blobs)
  landen in het `valuestr`-veld i.p.v. `value`. Voor zuivere tijd-aggregaties
  filter je op numerieke topics.
- Pin de image (`tdengine/tdengine:3.3.5.0`) i.p.v. `:latest` voor reproduceerbaarheid.

Volledige afweging: [`research/idp/2026-06-30-tdengine-vs-monstermq-store-layer-ai-data-layer.md`](../../strategy-os/user-workspace/research/idp/2026-06-30-tdengine-vs-monstermq-store-layer-ai-data-layer.md)
