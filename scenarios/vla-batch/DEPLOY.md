# Vla Batch v2 — VPS deploy-checklist

> Doel: de vla-batch demo live op één beveiligde URL, op een goedkope Ubuntu VPS (~2 GB).
> Stack = slim-base (`docker-compose.slim.yml`) + overlay (`scenarios/vla-batch/docker-compose.vla.yml`).
> Helper-script: [`deploy.sh`](deploy.sh) (`up` / `verify` / `smoke` / `fallback` / `logs` / `down`).
> Architectuur: fabriek-als-OPC-UA → **MonsterMQ native OPC-UA-client** (ingest) → UNS → MongoDB + TDengine → Grafana/BIRT/dashboard. Zie [`README.md`](README.md).

---

## ★ Zo draait het nu op `techflow-vps` (stand 2026-10-22)

**Ja, het is één compose-stack:** project `idp-os`, opgebouwd uit drie bestanden in
`/root/idp-os`. Eén script beheert hem: [`deploy/stack.sh`](deploy/stack.sh).

```bash
S=/root/idp-os/scenarios/vla-batch/deploy/stack.sh
$S status                 # diensten + 4 http-checks (lokaal, Grafana, publiek, auth)
$S offline                # alles stoppen; containers, volumes, netwerk blijven
$S online                 # alles starten in volgorde + model + ingress + check
$S online --build         # idem, maar images opnieuw bouwen (na code-wijziging)
$S backup                 # → /root/backups/idp-os/<datum>   (--met-historian: + TDengine)
$S restore <map>          # back-up terugzetten, daarna vanzelf online
$S remove                 # containers + netwerk weg, volumes blijven
```

**Diensten (13, in startvolgorde)**

| Dienst | Rol | Poort op de host | Volume |
|---|---|---|---|
| `mongo` | orders, batches, events, HU's | 127.0.0.1:27017 | `idp-os_mongo-data` (~1,8 GB) |
| `monstermq` | de datalaag (topics, ISA-95-context) | geen | `idp-os_monstermq-data` |
| `vla-tdengine` | historian | 127.0.0.1:6060 | `idp-os_vla-tdengine-data` (~3 GB) |
| `vla-factory`, `vla-factory-l2` | gesimuleerde lijnen L1/L2 (OPC-UA) | geen | — |
| `vla-opcua-init`, `vla-l2-opcua-init` | eenmalig: koppelt de lijnen aan de datalaag (exit 0 is goed) | — | — |
| `vla-batch-engine` | MES, autopilot, datalaag-API, oorzaakanalyse | geen | — |
| `vla-tdengine-bridge` | schrijft topics naar de historian | geen | — |
| `vla-ollama` | lokaal taalmodel `qwen2.5:1.5b`, `mem_limit 2g` | geen | `idp-os_vla-ollama-data` (model, wordt bij `online` opnieuw gehaald als hij ontbreekt) |
| `vla-ai` | AI-dienst op poort 8080 | 127.0.0.1:8095 | — |
| `grafana` | dashboards op `/grafana` | 127.0.0.1:3001 | `idp-os_grafana-data` |
| `vla-ui` | Next.js: site, `/site`-twin, analyse | 127.0.0.1:8091 | — |

Bewust **niet** gestart (staan wel in de compose-bestanden): `fastapi`, `vla-dashboard`,
`vla-tdgpt` (1,5 GB, past niet). Daarom start `stack.sh` een vaste lijst met `--no-deps`;
een kale `docker compose up -d` start ze mee.

**Ingress.** 80/443 zijn van de container `aios-caddy` (een andere stack). Ons blok in diens
Caddyfile wordt geschreven door [`deploy/attach-ingress-caddy.sh`](deploy/attach-ingress-caddy.sh)
(idempotent, valideert, draait terug bij een fout). `stack.sh online` roept hem aan.
- `/` → `vla-ui:3000`, achter basic auth (`/root/idp-os/.milkdemo-auth`, alleen op de VPS)
- `/api/ui/model` → zonder login (healthcheck)
- `/grafana*` → `grafana:3000`, achter dezelfde basic auth **én** de eigen Grafana-login. De
  proxy haalt de `Authorization`-header weg, zodat het demowachtwoord nooit een Grafana-login is.
- ⚠ Nooit de Caddyfile met de hand bewerken: bind-mount op een inode.

**Beveiliging (gecontroleerd 2026-10-22, van buitenaf).** Alle interne poorten zijn dicht;
de 0.0.0.0-poorten van `aios-openfga` en `portainer` worden door `DOCKER-USER` gedropt
(UFW ziet Docker-verkeer niet, test dus altijd met een TCP-connect van buiten). Grafana:
anoniem uit, sign-up uit, eigen basic-auth uit, cookie `Secure` + `SameSite=Strict`, geen
externe snapshots. `admin/admin` en TDengine `root/taosdata` worden geweigerd.

**Secrets** staan alleen op de VPS: `/root/idp-os/.env` en `/root/idp-os/.milkdemo-auth`.
`stack.sh backup` neemt ze mee (chmod 600) maar ze gaan nooit in git.

### Offline halen en terugzetten

| Wat je wilt | Doe | Terug |
|---|---|---|
| Tijdelijk uit (RAM vrij) | `$S offline` | `$S online` |
| Containers echt weg, data houden | `$S backup` → `$S remove` | `$S online` |
| Alles weg, ook volumes | `$S backup` → `scp -r techflow-vps:/root/backups/idp-os/<map> .` → `$S remove` → `docker volume rm $(docker volume ls -q \| grep ^idp-os_)` | zie *Nieuwe machine* |
| Nieuwe machine | Docker installeren, back-upmap erheen kopiëren, `mkdir -p /root/idp-os && tar xzf <map>/code.tgz -C /root/idp-os`, dan `$S restore <map>` | — |

- **Gemeten op 2026-10-22:** `offline` 22 s (publiek daarna 502), `online` 37 s tot alles
  200 geeft. Back-up ~7 min en 1,2 GB, waarvan 1,1 GB mongo: het archief
  `dw_uns_archivearchive` (55 mln. rijen, 11 GB ongecomprimeerd, TTL 1 jaar) is het grootste deel.
  De testback-up `/root/backups/idp-os/test-2026-10-22` is gecontroleerd met `gzip -t`,
  `mongorestore --dryRun` en `tar -tzf`.
- Na `online` duurt het ~1 min voor de site antwoordt; de autopilot vult het magazijn
  daarna in ~12 min weer op. Logistieke ritten staan in geheugen en beginnen opnieuw.
- Zonder `--met-historian` start de historian leeg en vult hij zich vanzelf; orders,
  batches, events en track & trace staan in mongo en gaan wél mee.
- Op een machine zonder `aios-caddy` moet je zelf een reverse proxy zetten met dezelfde
  drie routes als hierboven.
- **Nachtelijke back-up staat aan** (sinds 2026-10-22, root-crontab):
  `0 2 * * * /root/idp-os/scenarios/vla-batch/deploy/stack.sh backup --nachtelijk >> /var/log/idp-os-backup.log 2>&1`.
  De VPS draait op UTC, dus dat is 04:00 in de zomer en 03:00 in de winter (NL). De back-up
  komt in `/root/backups/idp-os/nacht/`; alleen de laatste drie blijven staan (`BEWAAR=3`).
  Opruimen gebeurt pas na een geslaagde back-up, en handmatige back-ups buiten `nacht/` raakt
  het nooit. Een `flock` voorkomt dat twee back-ups tegelijk draaien. Grafana ligt elke nacht
  ~10 s stil. **De back-ups staan op dezelfde VPS**: kopieer er af en toe een naar buiten
  (`scp -r techflow-vps:/root/backups/idp-os/nacht/<map> .`).
- De cron `hoststats.sh` (elke minuut) voedt het DataOps-tabblad en mag blijven staan.

**Code uitrollen.** `/root/idp-os` is geen git-checkout. Gewijzigde bestanden gaan per
`scp`/tar erheen, daarna `$S online --build` (of gericht:
`docker compose -p idp-os -f … up -d --no-deps --build vla-ui vla-batch-engine`). Zet bij
de engine `GIT_SHA=<sha>` ervoor, anders toont de UI "onbekend".

> De secties hieronder beschrijven de oorspronkelijke installatie (Traefik, later host-nginx).
> Ze zijn historisch; voor de VPS geldt bovenstaand blok.

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
| Dashboard / site-twin | `https://milkdemo.<domain>` en `/site` (basic-auth) |
| Grafana | `https://milkdemo.<domain>/grafana/` (basic-auth + Grafana-login) |
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
