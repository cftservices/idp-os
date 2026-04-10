# Industrial Data Platform

Open-source industrial data platform — deploy on any VPS in one command.

## Stack

| Service | Image | URL |
|---------|-------|-----|
| Traefik | `traefik:v3` | internal |
| Portainer | `portainer/portainer-ce` | `portainer.techflow24.com` |
| Mosquitto | `eclipse-mosquitto:2` | port 1883 |
| RabbitMQ | `rabbitmq:3-management` | `rabbitmq.techflow24.com` |
| MongoDB | `mongo:7` | internal |
| FastAPI | custom | `api.techflow24.com` |
| N8N | `n8nio/n8n` | `n8n.techflow24.com` |
| Grafana | `grafana/grafana` | `grafana.techflow24.com` |

## Deploy on Hostinger KVM2

### 1. Prerequisites (run on your VPS)

```bash
apt update && apt install -y docker.io docker-compose-plugin git
systemctl enable docker && systemctl start docker
```

### 2. DNS — point these A records to your VPS IP

```
grafana.techflow24.com
api.techflow24.com
n8n.techflow24.com
rabbitmq.techflow24.com
portainer.techflow24.com
```

### 3. Clone and configure

```bash
git clone <repo-url>
cd industrial-data-platform
cp .env.example .env
nano .env              # fill in your values
chmod 600 traefik/acme.json
```

### 4. Deploy

```bash
docker compose up -d
```

SSL certificates are provisioned automatically by Traefik + Let's Encrypt.

### 5. Access

| Service | URL |
|---------|-----|
| Grafana | https://grafana.techflow24.com |
| API docs | https://api.techflow24.com/docs |
| N8N | https://n8n.techflow24.com |
| RabbitMQ | https://rabbitmq.techflow24.com |
| Portainer | https://portainer.techflow24.com |

## Test the stack

```bash
# Check all containers running
docker compose ps

# Check API health
curl https://api.techflow24.com/health

# Send a test MQTT message
mosquitto_pub -h <VPS-IP> -p 1883 -t "plant/line1/pump1/current" -m "12.4"

# Write a test tag via API
curl -X POST https://api.techflow24.com/tags \
  -H "Content-Type: application/json" \
  -d '{"tag": "pump1.current", "value": 12.4, "unit": "A", "source": "test"}'
```

## Data flow

```
[PLC/SCADA/Sensor]
      ↓ MQTT publish
[Mosquitto broker]
      ↓ subscribe
[N8N workflow] — filter / transform / route
      ↓                    ↓
[RabbitMQ]           [MongoDB]
(event queue)        (storage)
      ↓                    ↓
[FastAPI REST layer] ←──────
      ↓
[Grafana] + [External consumers / AI]
```

## Architecture decisions

- **Traefik v3** — handles all SSL and routing; no manual nginx config
- **MongoDB internal only** — not exposed publicly, accessed via FastAPI
- **NiFi excluded** — too heavy (~2GB RAM); N8N + RabbitMQ covers the same use cases
- **Target VPS** — Hostinger KVM2 (2 vCPU, 8GB RAM); estimated RAM usage ~2.5GB
