#!/usr/bin/env bash
# --------------------------------------------------------------------------
# Beheer van de milkdemo-stack (DairyWorks vla-batch) op de VPS.
#
# Alles draait als één compose-project `idp-os`, opgebouwd uit drie bestanden:
#   docker-compose.slim.yml                       (basis: mongo, monstermq, grafana)
#   scenarios/vla-batch/docker-compose.vla.yml    (de vla-diensten)
#   scenarios/vla-batch/docker-compose.vps-shared.yml (VPS-overrides, poorten op 127.0.0.1)
#
# Gebruik (op de VPS, vanaf elke map):
#   stack.sh status              wat draait er, en antwoordt de site
#   stack.sh offline             alles stoppen; containers, volumes en netwerk blijven
#   stack.sh online [--build]    alles starten in de juiste volgorde + ingress + check
#   stack.sh backup [map]        mongodump + grafana + monstermq + code + secrets
#                                (--met-historian: ook TDengine, ~3 GB)
#   stack.sh restore <map>       een back-up terugzetten en daarna online brengen
#   stack.sh remove              containers en netwerk weg, volumes BLIJVEN
#
# Waarom een vaste dienstenlijst met --no-deps: de compose-bestanden kennen ook
# diensten die hier bewust niet draaien (fastapi, vla-dashboard, vla-tdgpt) en
# grafana hangt in de basis aan fastapi. Een kale `up -d` start die mee.
#
# Waarom `offline` = stop en geen down: aios-caddy hangt aan idp-network. Een
# down wil dat netwerk weghalen en botst op die koppeling.
# --------------------------------------------------------------------------
set -euo pipefail

ROOT=${IDP_ROOT:-/root/idp-os}
PROJECT=idp-os
NET=idp-network
OLLAMA_MODEL=${OLLAMA_MODEL:-qwen2.5:1.5b}
BACKUP_BASE=${BACKUP_BASE:-/root/backups/idp-os}
SITE=${SITE:-https://milkdemo.techflow24.com}

# Startvolgorde: opslag en bus eerst, dan de fabriek, dan wat erop leest.
SERVICES=(
    mongo monstermq vla-tdengine
    vla-factory vla-opcua-init vla-factory-l2 vla-l2-opcua-init
    vla-batch-engine vla-tdengine-bridge
    vla-ollama vla-ai grafana vla-ui
)
# Volumes die als tar worden bewaard (mongo gaat via mongodump).
TAR_VOLUMES=(grafana-data monstermq-data)
HISTORIAN_VOLUMES=(vla-tdengine-data)

cd "$ROOT"
DC=(docker compose -p "$PROJECT"
    -f docker-compose.slim.yml
    -f scenarios/vla-batch/docker-compose.vla.yml
    -f scenarios/vla-batch/docker-compose.vps-shared.yml)

groen(){ printf "\033[32m%s\033[0m\n" "$*"; }
geel(){ printf "\033[33m%s\033[0m\n" "$*"; }
rood(){ printf "\033[31m%s\033[0m\n" "$*" >&2; }

http_code(){ curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$@" || echo 000; }

cmd_status(){
    "${DC[@]}" ps -a --format 'table {{.Service}}\t{{.Status}}'
    echo
    echo "vla-ui   (127.0.0.1:8091/api/ui/model): $(http_code http://127.0.0.1:8091/api/ui/model)"
    echo "grafana  (127.0.0.1:3001/api/health):   $(http_code http://127.0.0.1:3001/api/health)"
    echo "publiek  (${SITE}/api/ui/model):        $(http_code "${SITE}/api/ui/model")"
    echo "publiek  (${SITE}/site, zonder login):  $(http_code "${SITE}/site")  (401 = goed)"
}

cmd_offline(){
    "${DC[@]}" stop
    groen "Offline. Containers, volumes en netwerk zijn bewaard."
    echo "De site geeft nu 502. Terug: $0 online"
}

zorg_voor_model(){
    if docker exec vla-ollama ollama list < /dev/null 2>/dev/null | grep -q "^${OLLAMA_MODEL}"; then
        echo "• model ${OLLAMA_MODEL} aanwezig"
    else
        echo "• model ${OLLAMA_MODEL} ophalen (~1 GB)"
        docker exec vla-ollama ollama pull "$OLLAMA_MODEL" < /dev/null
    fi
}

cmd_online(){
    local build=()
    [ "${1:-}" = "--build" ] && build=(--build)
    [ -f .env ] || { rood "Geen ${ROOT}/.env. Zet die eerst terug (zie restore)."; exit 1; }
    GIT_SHA=${GIT_SHA:-$(cat .deployed-sha 2>/dev/null || echo onbekend)} \
        "${DC[@]}" up -d --no-deps "${build[@]}" "${SERVICES[@]}"
    zorg_voor_model
    if docker ps --format '{{.Names}}' | grep -qx aios-caddy; then
        bash scenarios/vla-batch/deploy/attach-ingress-caddy.sh
    else
        geel "aios-caddy draait niet: geen publieke route. Zie DEPLOY.md, 'Ingress'."
    fi
    echo "• wachten tot de UI antwoordt"
    for _ in $(seq 1 30); do
        [ "$(http_code http://127.0.0.1:8091/api/ui/model)" = 200 ] && break
        sleep 4
    done
    cmd_status
}

volume_tar(){  # volume_tar <volume-zonder-prefix> <doelmap>
    docker run --rm -v "${PROJECT}_$1:/v:ro" -v "$2:/b" alpine \
        tar czf "/b/$1.tgz" -C /v . < /dev/null
}

cmd_backup(){
    local historian=0 doel=""
    for a in "$@"; do
        case "$a" in --met-historian) historian=1 ;; *) doel="$a" ;; esac
    done
    doel=${doel:-${BACKUP_BASE}/$(date +%Y-%m-%d_%H%M)}
    mkdir -p "$doel"; chmod 700 "$doel"
    echo "→ ${doel}"

    echo "• mongodump (online, consistent)"
    docker exec mongo sh -c 'mongodump --quiet -u "$MONGO_INITDB_ROOT_USERNAME" \
        -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --archive --gzip' \
        < /dev/null > "$doel/mongo.archive.gz"

    # Grafana bewaart in SQLite: kort stoppen voor een consistente kopie.
    echo "• grafana + monstermq (grafana ~10 s offline)"
    # Ook bij een mislukte tar moet de dienst weer starten, dus geen set -e hier.
    local fout=0
    "${DC[@]}" stop grafana >/dev/null
    for v in "${TAR_VOLUMES[@]}"; do volume_tar "$v" "$doel" || fout=1; done
    "${DC[@]}" start grafana >/dev/null

    if [ "$historian" = 1 ]; then
        echo "• historian (TDengine kort offline)"
        "${DC[@]}" stop vla-tdengine-bridge vla-tdengine >/dev/null
        for v in "${HISTORIAN_VOLUMES[@]}"; do volume_tar "$v" "$doel" || fout=1; done
        "${DC[@]}" start vla-tdengine vla-tdengine-bridge >/dev/null
    fi
    [ "$fout" = 0 ] || { rood "Een volume-tar is mislukt; back-up onvolledig."; exit 1; }

    echo "• code + secrets"
    tar czf "$doel/code.tgz" --exclude=node_modules --exclude=.next \
        --exclude='*.bak*' --exclude=.env --exclude=.milkdemo-auth -C "$ROOT" .
    install -m 600 .env "$doel/env"
    [ -f .milkdemo-auth ] && install -m 600 .milkdemo-auth "$doel/milkdemo-auth"

    {
        echo "datum=$(date -Iseconds)"
        echo "historian=${historian}"
        echo "ollama_model=${OLLAMA_MODEL}"
        echo "deployed_sha=$(cat .deployed-sha 2>/dev/null || echo onbekend)"
        docker exec mongo sh -c 'mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" \
            -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval \
            "db.getMongo().getDBNames().filter(d=>![\"admin\",\"local\",\"config\"].includes(d)).forEach(n=>{const d=db.getSiblingDB(n);d.getCollectionInfos({type:{\$in:[\"collection\",\"timeseries\"]}}).map(i=>i.name).filter(c=>!c.startsWith(\"system.\")).sort().forEach(c=>{let v;try{v=d[c].estimatedDocumentCount()}catch(e){v=\"?\"};print(\"mongo.\"+n+\".\"+c+\"=\"+v)})})"' \
            < /dev/null || echo "mongo.telling=mislukt"
    } > "$doel/MANIFEST"
    chmod 600 "$doel"/*
    du -sh "$doel"
    groen "Back-up klaar. Bewaar hem ook buiten de VPS:"
    echo "  scp -r techflow-vps:${doel} ."
}

volume_terug(){  # volume_terug <volume-zonder-prefix> <bronmap>
    local vol="${PROJECT}_$1"
    docker volume inspect "$vol" >/dev/null 2>&1 || docker volume create \
        --label com.docker.compose.project="$PROJECT" \
        --label com.docker.compose.volume="$1" "$vol" >/dev/null
    docker run --rm -v "$vol:/v" -v "$2:/b:ro" alpine sh -c \
        "find /v -mindepth 1 -delete && tar xzf /b/$1.tgz -C /v" < /dev/null
}

cmd_restore(){
    local bron=${1:?gebruik: $0 restore <back-upmap>}
    [ -f "$bron/MANIFEST" ] || { rood "Geen MANIFEST in ${bron}."; exit 1; }
    cat "$bron/MANIFEST" | head -4
    read -r -p "Dit overschrijft de huidige data van ${PROJECT}. Doorgaan? [ja/N] " ok
    [ "$ok" = ja ] || { echo "Afgebroken."; exit 1; }

    [ -f .env ] || install -m 600 "$bron/env" .env
    [ -f .milkdemo-auth ] || { [ -f "$bron/milkdemo-auth" ] && install -m 600 "$bron/milkdemo-auth" .milkdemo-auth; } || true

    "${DC[@]}" stop >/dev/null 2>&1 || true
    for v in "${TAR_VOLUMES[@]}" "${HISTORIAN_VOLUMES[@]}"; do
        [ -f "$bron/$v.tgz" ] && { echo "• volume ${v}"; volume_terug "$v" "$bron"; }
    done

    echo "• mongo"
    "${DC[@]}" up -d --no-deps mongo
    for _ in $(seq 1 30); do
        docker exec mongo mongosh --quiet --eval 'db.runCommand({ping:1}).ok' < /dev/null 2>/dev/null | grep -q 1 && break
        sleep 2
    done
    docker exec -i mongo sh -c 'mongorestore --quiet --drop -u "$MONGO_INITDB_ROOT_USERNAME" \
        -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --archive --gzip' \
        < "$bron/mongo.archive.gz"

    cmd_online
    groen "Hersteld uit ${bron}."
}

cmd_remove(){
    read -r -p "Containers en netwerk van ${PROJECT} weghalen (volumes blijven)? [ja/N] " ok
    [ "$ok" = ja ] || { echo "Afgebroken."; exit 1; }
    docker network disconnect "$NET" aios-caddy < /dev/null 2>/dev/null || true
    "${DC[@]}" down
    groen "Weg. Volumes staan er nog (docker volume ls | grep ${PROJECT}). Terug: $0 online"
}

case "${1:-}" in
    status) cmd_status ;;
    offline) cmd_offline ;;
    online) shift; cmd_online "$@" ;;
    backup) shift; cmd_backup "$@" ;;
    restore) shift; cmd_restore "$@" ;;
    remove) cmd_remove ;;
    *) sed -n '2,22p' "$0"; exit 1 ;;
esac
