#!/usr/bin/env bash
# Deploy the Willhaben host service to the docker host:  host_service/deploy.sh
# Prints nothing secret; the TOKEN stays in ~/willhaben-host/.env on the host.
set -euo pipefail
cd "$(dirname "$0")"
HOST=${HOST:-docker}
DIR=${DIR:-willhaben-host}

ssh "$HOST" "mkdir -p $DIR/data && chown 1000:1000 $DIR/data 2>/dev/null || true"
rsync -a Dockerfile docker-compose.yml server.py "$HOST:$DIR/"

# First deploy: create .env with a fresh TOKEN. Read it with: ssh docker cat willhaben-host/.env
ssh "$HOST" "cd $DIR && [ -f .env ] || { umask 077; echo \"TOKEN=\$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')\" > .env; }"
ssh "$HOST" "cd $DIR && docker compose up -d --build && docker compose ps"
ssh "$HOST" "sleep 2; curl -fsS http://192.168.10.241:3013/healthz && echo"
