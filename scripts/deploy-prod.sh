#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -p fantasy-prod -f docker-compose.deploy.yml)

show_status_on_error() {
    echo "Production deployment failed. Current container status:" >&2
    "${COMPOSE[@]}" ps >&2 || true
}

trap show_status_on_error ERR

if [[ ! -f .env ]]; then
    echo "Missing .env in $ROOT" >&2
    exit 1
fi

APP_PORT="$(awk -F= '$1 == "APP_PORT" { print $2; exit }' .env)"
if [[ -z "$APP_PORT" ]]; then
    echo "APP_PORT is missing from $ROOT/.env" >&2
    exit 1
fi
if [[ ! "$APP_PORT" =~ ^[0-9]+$ ]]; then
    echo "APP_PORT must be numeric: $APP_PORT" >&2
    exit 1
fi

echo "Validating Compose configuration..."
"${COMPOSE[@]}" config --quiet

echo "Building and starting production..."
"${COMPOSE[@]}" up -d --build --wait --wait-timeout 120

echo "Applying migrations..."
"${COMPOSE[@]}" run --rm web python manage.py migrate --noinput

echo "Checking production health..."
if ! curl --fail --silent --show-error --retry 10 --retry-delay 2 \
    --connect-timeout 3 --max-time 10 \
    "http://127.0.0.1:${APP_PORT}/healthz"; then
    echo "Production health check failed" >&2
    "${COMPOSE[@]}" ps
    exit 1
fi
printf '\n'

echo "Production containers:"
"${COMPOSE[@]}" ps