# Operations

> How to deploy, configure, size, and troubleshoot the stack. For the quick
> start, see the repo [`README.md`](../README.md#run-it-locally). This doc is the
> fuller operator reference.

## Deploy

```bash
git clone https://github.com/cftservices/idp-os.git
cd idp-os
cp .env.example .env          # fill in the variables below
chmod 600 traefik/acme.json   # Let's Encrypt store must be private
docker compose -f docker-compose.v3.yml up -d
```

First boot takes 2-3 minutes — NiFi and RabbitMQ have generous healthcheck
start-periods. Use `docker-compose.v3.yml` for anything you publish; the other
compose files are the v2 baseline (`docker-compose.yml`), an IDP-only subset
(`docker-compose.idp.yml`), and dev overrides (`docker-compose.dev.yml`).

## Environment variables

Set in `.env` (never commit it). Names come from `docker-compose.v3.yml`.

| Variable | Used by | Notes |
|----------|---------|-------|
| `DOMAIN` | Traefik routers | Base domain; subdomains (`grafana.`, `api.`, `nifi.`, …) route off it |
| `RABBITMQ_USER` / `RABBITMQ_PASS` | RabbitMQ, N8N, NiFi, FastAPI, packml-sims | AMQP + management login |
| `MONGO_INITDB_ROOT_USERNAME` / `_PASSWORD` | MongoDB | Must match the credentials in `monstermq/config.yaml` |
| `MONGO_URL` / `MONGO_DB` / `MONGODB_URI` / `MONGODB_DATABASE` | FastAPI, webapp | Connection strings for the two consumers |
| `NIFI_USERNAME` / `NIFI_PASSWORD` | NiFi | Single-user creds |
| `NIFI_JVM_HEAP_MAX` | NiFi | `512m` on a 4 GB box, `1g`+ on bigger |
| `GRAFANA_ADMIN_USER` / `_PASSWORD` | Grafana | Admin login |
| `NEO4J_AUTH` | Neo4j, FastAPI | Format `neo4j/<password>` |
| `API_SECRET_KEY` | FastAPI | App secret |
| `N8N_USER` / `N8N_PASSWORD` | N8N | Basic auth |

## VPS sizing

The full v3 stack needs ~3 GB RAM minimum, ~5.5 GB comfortable. NiFi is the
heaviest single component.

| VPS class | RAM | Verdict |
|-----------|-----|---------|
| 2 GB | 2 GB | ❌ Too small for NiFi |
| 4 GB (Hetzner CX22 / Hostinger KVM2) | 4 GB | ⚠️ NiFi at `-Xmx512m`, limited flows — the €8/month proof config |
| 8 GB (Hetzner CX32) | 8 GB | ✅ Full stack + NiFi 1 GB heap |
| 16 GB (Hetzner CX42) | 16 GB | ✅ Production-grade; demo + students |

> The €8/month claim runs on the 4 GB class. It works because of
> [DataOps discipline](dataops-for-ot.md), not because the stack is minimal.

## Verify a deploy

```bash
docker compose -f docker-compose.v3.yml ps
docker compose -f docker-compose.v3.yml logs -f nifi rabbitmq monstermq

# Re-register the OPC-UA devices in MonsterMQ (one-shot, idempotent)
docker compose -f docker-compose.v3.yml run --rm init-opcua
```

From the TechFlow-OS hub, `/idp-status` runs HTTP health-checks against the live
endpoints on `techflow24.com` — no SSH or Docker required.

## Access (with `DOMAIN` configured)

| UI | URL | Local fallback |
|----|-----|----------------|
| Webapp (live dashboard) | `https://${DOMAIN}` | — |
| Grafana | `https://grafana.${DOMAIN}` | — |
| FastAPI docs | `https://api.${DOMAIN}/docs` | — |
| NiFi | `https://nifi.${DOMAIN}` | `https://localhost:8443/nifi` |
| RabbitMQ management | `https://mqtt.${DOMAIN}` | `http://localhost:15672` |
| Portainer | `https://portainer.${DOMAIN}` | — |
| Neo4j browser | — | `http://localhost:7474` |
| N8N | — | `http://localhost:5678` |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| NiFi container OOM-killed or restarting | Heap too large for the box | Lower `NIFI_JVM_HEAP_MAX` to `512m` |
| MonsterMQ can't reach MongoDB | Credentials drift between `.env` and `monstermq/config.yaml` | Make the two match — config has hardcoded creds |
| Traefik serves a self-signed / no cert | `traefik/acme.json` wrong perms, or DNS not pointed at host | `chmod 600 traefik/acme.json`; confirm `DOMAIN` A-record |
| No OPC-UA data in MongoDB | Devices not registered after a fresh volume | `run --rm init-opcua` |
| "Mosquitto" / "SQL Server" referenced somewhere | Stale pre-v3 doc | Ignore; verify against `docker-compose.v3.yml` |

## Backups

- **MongoDB** — `docker compose exec mongo mongodump` to a mounted path; the
  `staging.*` layer is the rederivation source of truth, so back it up first.
- **Neo4j** — dump `neo4j-data` volume (the ISA-95 graph is hand-built).
- **Config-as-data** — `traefik/`, `monstermq/`, `config/rabbitmq/`,
  `grafana/provisioning/` are all in git already; that *is* the backup.

## Optional: TDengine store-layer overlay

To try TDengine alongside MongoDB without touching the main stack, see
[`../tdengine-poc/README.md`](../tdengine-poc/README.md). It attaches to the
running `idp-network` and changes nothing in `docker-compose.v3.yml`.
