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

## 6. Beheer

- [ ] Logs: `./scenarios/vla-batch/deploy.sh logs [service]`
- [ ] Stoppen (volumes behouden): `./scenarios/vla-batch/deploy.sh down`
- [ ] Volledig wissen (incl. data): `docker compose -f docker-compose.slim.yml -f scenarios/vla-batch/docker-compose.vla.yml down -v`
- [ ] Update: `git pull` → `./scenarios/vla-batch/deploy.sh up` (herbouwt gewijzigde images).

## 7. Anonimisering (vóór delen/demo)

- [ ] Alleen `DairyWorks`/generieke namen zichtbaar (dashboard, Grafana, rapport). Geen een NL-zuivelbedrijf/[geanonimiseerd]/ICT.
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
