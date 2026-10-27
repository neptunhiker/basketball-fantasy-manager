#!/usr/bin/env bash
# Runs on the server, inside the clone for the given environment.
# Usually called by scripts/ship.sh from a laptop.
set -Eeuo pipefail

ENV="${1:-}"
if [[ "$ENV" != staging && "$ENV" != prod ]]; then
    echo "usage: deploy.sh staging|prod" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -p "fantasy-$ENV" -f docker-compose.deploy.yml)

show_status_on_error() {
    echo "$ENV deployment failed. Current container status:" >&2
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

for variable in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
    if ! grep -qE "^${variable}=.+$" .env; then
        echo "$variable is missing from $ROOT/.env" >&2
        exit 1
    fi
done

echo "Deploying $(git rev-parse --short HEAD) to $ENV..."

echo "Validating Compose configuration..."
"${COMPOSE[@]}" config --quiet

echo "Building images..."
"${COMPOSE[@]}" build

"${COMPOSE[@]}" up -d --wait db

if [[ "$ENV" == prod ]]; then
    echo "Backing up database..."
    mkdir -p backups
    BACKUP="backups/$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD).sql.gz"
    "${COMPOSE[@]}" exec -T db sh -c 'pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"' \
        | gzip > "$BACKUP"
    echo "Wrote $BACKUP"
    # Keep the 14 most recent backups.
    ls -1t backups/*.sql.gz | tail -n +15 | xargs -r rm --
fi

# Migrate with the new image before it takes traffic, so new code never
# runs against the old schema.
echo "Applying migrations..."
"${COMPOSE[@]}" run --rm web python manage.py migrate --noinput

echo "Starting $ENV..."
"${COMPOSE[@]}" up -d --wait --wait-timeout 120

echo "Checking $ENV health..."
if ! curl --fail --silent --show-error --retry 10 --retry-delay 2 \
    --connect-timeout 3 --max-time 10 \
    "http://127.0.0.1:${APP_PORT}/healthz"; then
    echo "$ENV health check failed" >&2
    "${COMPOSE[@]}" ps
    exit 1
fi
printf '\n'

docker image prune -f >/dev/null
# Cap the build cache at 2 GB; it otherwise grows with every build.
# --keep-storage is the older name for --max-used-space.
docker builder prune -f --max-used-space 2gb >/dev/null 2>&1 \
    || docker builder prune -f --keep-storage 2gb >/dev/null 2>&1 \
    || echo "Build cache prune failed, continuing" >&2

echo "$ENV containers:"
"${COMPOSE[@]}" ps
