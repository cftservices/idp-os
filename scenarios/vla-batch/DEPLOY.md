# Vla Batch v2 — VPS deploy-checklist

> Doel: de vla-batch demo live op één beveiligde URL, op een goedkope Ubuntu VPS (~2 GB).
> Stack = slim-base (`docker-compose.slim.yml`) + overlay (`scenarios/vla-batch/docker-compose.vla.yml`).
> Helper-script: [`deploy.sh`](deploy.sh) (`up` / `verify` / `smoke` / `fallback` / `logs` / `down`).
> Architectuur: fabriek-als-OPC-UA → **MonsterMQ native OPC-UA-client** (ingest) → UNS → MongoDB + TDengine → Grafana/BIRT/dashboard. Zie [`README.md`](README.md).

---

## 0. Vooraf (eenmalig)

- [ ] **VPS**: Ubuntu 22.04/24.04, ≥ 2 GB RAM (TDengine + 8 containers; 4 GB comfortabeler), ≥ 20 GB disk.
- [ ] **Docker + Compose v2**:
      ```bash
      curl -fsSL https://get.docker.com | sh
      sudo usermod -aG docker $USER   # opnieuw inloggen
      docker compose version          # v2.x?
      ```
- [ ] **DNS** (A-records → VPS-IP), subdomeinen van je `DOMAIN`:
      - `milkdemo.<domain>`  → dashboard
      - `grafana.<domain>`   → Grafana
- [ ] **Firewall**: open **80** + **443** (Traefik/Let's Encrypt) en **22** (SSH). **Niet** openen: 1883 (MQTT), 4840 (OPC-UA), 27017 (Mongo), 6041 (TDengine), 4000 (MonsterMQ admin) — die blijven intern op `idp-network`.
- [ ] **Repo op de VPS** (idp-os): `git clone` of rsync naar bv. `~/idp-os`. Werk vanuit die root.

## 1. Configuratie (`.env`)

- [ ] Kopieer en vul in:
      ```bash
      cp scenarios/vla-batch/.env.example .env
      ```
- [ ] `DOMAIN=` jouw domein (bv. `techflow24.com`).
- [ ] `TRAEFIK_ACME_EMAIL=` echt e-mailadres (Let's Encrypt) — **niet** de placeholder.
- [ ] **Wachtwoorden wijzigen** (niet de demo-defaults live zetten): `MONGO_INITDB_ROOT_PASSWORD` (+ zelfde in `MONGO_URL`), `GRAFANA_ADMIN_PASSWORD`, `TD_PASS` (indien aanpasbaar), `API_SECRET_KEY`.
- [ ] **Dashboard basic-auth** — genereer een bcrypt-hash en verdubbel elke `$`:
      ```bash
      sudo apt-get install -y apache2-utils
      htpasswd -nbB demo 'sterk-wachtwoord'      # bv. demo:$2y$05$....
      ```
      Zet in `.env` als `DASHBOARD_AUTH=demo:$$2y$$05$$....` (elke `$` → `$$`).
- [ ] **Grafana TDengine-plugin**: de overlay zet dit al via `GF_INSTALL_PLUGINS`, maar verifieer dat de plugin `tdengine-datasource` geïnstalleerd wordt (zie `scenarios/vla-batch/grafana/provisioning/datasources/tdengine.yaml`). Zo niet: voeg `tdengine-datasource` toe aan `GF_INSTALL_PLUGINS`.

## 2. Deploy

- [ ] Vanuit de idp-os root:
      ```bash
      chmod +x scenarios/vla-batch/deploy.sh
      ./scenarios/vla-batch/deploy.sh up
      ```
      Dit doet: `docker compose -f docker-compose.slim.yml -f scenarios/vla-batch/docker-compose.vla.yml up -d --build`, wacht op MonsterMQ, en de one-shot **`vla-opcua-init`** registreert het OPC-UA-device (`addOpcUaDevice`, 24 tags → UNS). Daarna draait automatisch `verify`.
- [ ] Eerste start duurt langer (images pullen/bouwen: asyncua, TDengine, reportlab). Let's Encrypt-cert kan 1-2 min duren.

## 3. Verifiëren

- [ ] `./scenarios/vla-batch/deploy.sh verify` — controleert:
      1. container-status (`ps`)
      2. OPC-UA-device `vla` geregistreerd in MonsterMQ (GraphQL `opcUaDevices`)
      3. `batch-engine` health (`/api/v1/health`)
      4. **UNS-flow**: `DairyWorks/Vla/#` publiceert (mosquitto_sub, 5 msgs)
      5. TDengine `idp.telemetry` bereikbaar/gevuld
- [ ] **Smoke-test** (één demo-batch end-to-end):
      ```bash
      ./scenarios/vla-batch/deploy.sh smoke
      ```
      Verwacht: state loopt `DOSING→COOKING→COOLING→FILLING→COMPLETE`, viscositeit ~260 cP, verdict `APPROVED`.
- [ ] **Browser**: `https://milkdemo.<domain>` (basic-auth) → live batch, viscositeit-gauge, SCADA-knoppen. `https://grafana.<domain>` → TDengine-trends.
- [ ] **Solve demonstreren**: in het SCADA/admin-paneel `InjectFault cook_undertemp` (magnitude ~0.6) → viscositeit zakt < 150 cP → gauge wordt rood → verdict `HOLD/REJECTED`. `ClearFault` herstelt.

## 4. ⚠ Bekende valkuil — OPC-UA ns-index

MonsterMQ's OPC-UA-client leest de fabriek via node-ids met **`ns=2`** (asyncua's eerste user-namespace). Als de effectieve namespace-index op de VPS afwijkt, komt er **geen** UNS-flow (verify stap 4 leeg).

- [ ] Check de index:
      ```bash
      ./scenarios/vla-batch/deploy.sh logs vla-factory   # zoek "namespace"/"ns="
      docker compose -f docker-compose.slim.yml -f scenarios/vla-batch/docker-compose.vla.yml logs monstermq | grep -i opcua
      ```
- [ ] Klopt de index niet? Pas de `ns=2` in `scenarios/vla-batch/monstermq-init/init-vla-opcua.sh` aan en her-registreer (`updateOpcUaDevice`), **of** schakel over op de fallback (stap 5).

## 5. Fallback — de connector

Werkt de MonsterMQ-native ingest niet meteen, gebruik de meegeleverde connector (zelfde OPC-UA↔UNS-brug, losse container):

- [ ] ```bash
      ./scenarios/vla-batch/deploy.sh fallback   # zet device 'vla' uit + start vla-connector (profile fallback)
      ./scenarios/vla-batch/deploy.sh verify
      ```
- [ ] De connector ontdekt/leest de nodes zelf; geen ns-index-config nodig.

## 5b. Variant — gedeelde VPS met host-nginx (zo draait techflow24.com)

Heeft de VPS al een reverse proxy op 80/443 (host-nginx + certbot) en is host-1883
bezet (legacy mosquitto), gebruik dan de extra overlay
[`docker-compose.vps-shared.yml`](docker-compose.vps-shared.yml):

- Traefik uit de slim-base wordt uitgeschakeld; MonsterMQ krijgt geen host-poort.
- `vla-dashboard` → `127.0.0.1:8090`, `grafana` → `127.0.0.1:3001`; host-nginx
  doet TLS + routing + basic-auth (`/etc/nginx/.htpasswd-milkdemo`).
- Alle deploy.sh commando's werken via env `VLA_EXTRA_COMPOSE`:
  ```bash
  export VLA_EXTRA_COMPOSE=scenarios/vla-batch/docker-compose.vps-shared.yml
  ./scenarios/vla-batch/deploy.sh up      # of verify / smoke / fallback
  ```
- ⚠ Draai `up` op zo'n VPS nooit ZONDER de overlay — Traefik zou 80/443 claimen
  en botsen met nginx.
- nginx-vhost + hardening + runbook: techflow-os hub-repo `infra/vps-techflow24/`
  (private).

### Demo-autopilot (alleen in deze overlay)

Op de VPS lopen batches vanzelf door, zonder operator. Lokaal staat dit uit.

- `vla-batch-engine`: `DEMO_AUTOPILOT=1` start om de ~2 min een MES-batch
  (`chocolate-vla-1L`, 10 s pauze na COMPLETE). Een CIP volgt vanzelf als de
  kookunit vuil is. Batches komen op opeenvolgende klantorders (6, 8, 10 of 12
  batches, leverdatum 2 uur na aanmaak, één van de vijf klanten); een order
  sluit zodra hij volledig is geproduceerd en de volgende klant begint.
  Autopilot-batches ouder dan 24 uur, of meer dan een uur vastgelopen in een
  lopende fase, worden elk uur opgeruimd.
- `vla-factory`: `AUTOSTART_DELAY_S=30` ketent pas zelf als er 30 s geen
  MES-batch kwam. Dat is het vangnet, zodat de bus nooit stilvalt.
- Status: `GET /api/v1/autopilot` (`last_action`, `batches_started`, `cip_runs`).
- Uitzetten: `DEMO_AUTOPILOT: "0"` in de overlay en `up -d vla-batch-engine`.
- **Magazijnstroom** (`vla/warehouse.py`, `DEMO_AUTOPILOT_WAREHOUSE`, standaard
  `1`): elke COMPLETE en APPROVED autopilot-batch van de laatste 15 min wordt
  gepalletiseerd in pallets van 480 packs (de laatste is kleiner), en elke ~9 s
  gaat er één pallet naar een willekeurig vrij vak (96 vakken; het `slot` staat
  op de HU). Na 12 min laadt er één truck tegelijk, één pallet per 6 s. Bij
  minder dan 12 vrije vakken laadt hij eerder, en een volle stelling houdt
  pallets bij de palletiser. Geshipte autopilot-HU's verdwijnen na 30 min.
  Steady state is ~65 van de 96 vakken. Na een uitrol vult de stelling zich
  geleidelijk; de eerste truck vertrekt na ~12 min. De twin (`/site`) speelt
  elke putaway en laadactie na met twee heftrucks. `RACK_SLOTS` staat in
  `warehouse.py` én in `ui/lib/twin/layout.ts` en moet gelijk blijven.
  Status: `warehouse_last` in `GET /api/v1/autopilot`.
- **Logistiek-simulatie** (`vla/logistics_sim.py`, `DEMO_AUTOPILOT_LOGISTICS`,
  standaard `1`): voegt aan elke zending een fictieve klant, bestemming en ETA
  toe, en daarnaast accu en ritten per heftruck en per grondstof een
  leverancier: een RVS-tankwagen voor melk, een bulkwagen voor suiker en
  zetmeel, en een palletwagen voor cacao. Zakt een silo onder 50% van zijn
  capaciteit (4× de bestelgrens), dan wordt er besteld; tijdens het lossen
  boekt de leverancier in delen echte goederenontvangsten (`inventory.receive`),
  begrensd op de capaciteit. Elke grondstof heeft een eigen losplaats
  (`INBOUND_BAYS` in `ui/lib/twin/flow.ts`). Uitgaande trailers aan het dock
  zijn doorzichtig en tonen de geladen pallets.
  De ritten staan in geheugen en beginnen na een herstart opnieuw. De twin leest
  ze via `GET /api/v1/logistics` (`shipments`, `forklifts`, `inbounds`,
  `inbound` = alleen melk, voor oude clients, en `source`).
  De sparklines in de overlay vullen zich in de browser; daar is geen
  historie-endpoint voor.
- Een batch die liep tijdens een engine-herstart heeft geen follow-thread meer.
  Hij blijft in zijn fase staan, maar blokkeert de autopilot niet.

## 6. Beheer

- [ ] Logs: `./scenarios/vla-batch/deploy.sh logs [service]`
- [ ] Stoppen (volumes behouden): `./scenarios/vla-batch/deploy.sh down`
- [ ] Volledig wissen (incl. data): `docker compose -f docker-compose.slim.yml -f scenarios/vla-batch/docker-compose.vla.yml down -v`
- [ ] Update: `git pull` → `./scenarios/vla-batch/deploy.sh up` (herbouwt gewijzigde images).

## 7. Anonimisering (vóór delen/demo)

- [ ] Alleen `DairyWorks`/generieke namen zichtbaar (dashboard, Grafana, rapport). Geen werkgever-/klant-/leverancier-namen.
- [ ] Demo-URL enkel achter basic-auth delen; interne poorten niet publiek (zie firewall).

---

### Snelle referentie

| Actie | Commando |
|------|----------|
| Deploy | `./scenarios/vla-batch/deploy.sh up` |
| Verifiëren | `./scenarios/vla-batch/deploy.sh verify` |
| Demo-batch | `./scenarios/vla-batch/deploy.sh smoke` |
| Fallback (connector) | `./scenarios/vla-batch/deploy.sh fallback` |
| Logs | `./scenarios/vla-batch/deploy.sh logs [svc]` |
| Stoppen | `./scenarios/vla-batch/deploy.sh down` |

| Service | Bereikbaar |
|---------|-----------|
| Dashboard | `https://milkdemo.<domain>` (basic-auth) |
| Grafana | `https://grafana.<domain>` |
| batch-engine / factory / TDengine / MonsterMQ | intern op `idp-network` (niet publiek) |

---

## Cutover naar de Next.js-UI (`vla-ui`)

De nieuwe UI draait bewust **naast** de oude SPA, zodat de demo blijft werken.
Drie gescheiden stappen, elk apart verifieerbaar.

### Stap 1: de middleware-verhuizing (doe dit eerst en apart)

`milkdemo-auth` werd gedefinieerd op `vla-dashboard`, de service die straks
verdwijnt, terwijl Grafana ernaar verwijst. Traefik leest labels alleen van
containers met `traefik.enable=true`, dus **verdwijnt die service, dan valt
Grafana om.** De definitie staat nu bij `grafana`, die elke dashboardwissel
overleeft.

In dezelfde wijziging kwam de same-origin embed-route erbij
(`Host(milkdemo) && PathPrefix(/grafana)`), plus `serve_from_sub_path` en
`allow_embedding`. `GF_AUTH_ANONYMOUS_ENABLED` blijft **uit**: een anonieme
bezoeker is Viewer, en een Viewer mag via de HTTP-API willekeurige queries naar
elke datasource sturen. Dat zou de complete historian publiek queryable maken.

```bash
docker compose -f docker-compose.slim.yml \
               -f scenarios/vla-batch/docker-compose.vla.yml up -d grafana vla-dashboard

# Verifieer VOORDAT je verder gaat:
curl -sk -o /dev/null -w '%{http_code}\n' https://grafana.${DOMAIN}/api/health          # 401
curl -sku "$USER:$PASS" -o /dev/null -w '%{http_code}\n' https://grafana.${DOMAIN}/api/health   # 200
curl -sku "$USER:$PASS" -o /dev/null -w '%{http_code}\n' https://milkdemo.${DOMAIN}/grafana/api/health  # 200
```

Combineer `serve_from_sub_path` niet met een prefix-strip in de proxy: die twee
doen hetzelfde werk dubbel en geven redirect-loops of 404's op statische assets.
Test daarna zowel een `d-solo`-iframe als `/grafana/api/live/`.

### Stap 2: `vla-ui` ernaast

```bash
docker compose -f docker-compose.slim.yml \
               -f scenarios/vla-batch/docker-compose.vla.yml up -d --build vla-ui

curl -sku "$USER:$PASS" https://milkdemo-next.${DOMAIN}/api/ui/model | head -c 200
```

De `/api`-proxy die nginx deed zit nu in `app/api/v1/[...path]/route.ts`,
inclusief de 60 s timeout die de PDF-renders nodig hebben en het ongewijzigd
doorgeven van de gestructureerde weigeringen. De oude SPA blijft draaien op
`milkdemo.${DOMAIN}`.

Loop de elf routes na: `/`, `/?view=sales`, `/management`, `/line`, `/alarms`,
`/batches`, `/equipment`, `/reports`, `/analyse`, `/scada`, `/shopfloor`.

### Stap 3: omschakelen

Laat de `milkdemo`-router naar `vla-ui` wijzen en schrap `vla-dashboard`.
Rollback is een `git revert` plus `docker compose up -d`.

**Voor de omschakeling nog even nalopen:** draait er een curl-loop op Grafana
tijdens de hele operatie zonder een enkele non-2xx, en is `NEXT_PUBLIC_GRAFANA_PATH`
gezet op een dashboard dat bestaat (`/grafana/d/vla-line?kiosk`).
