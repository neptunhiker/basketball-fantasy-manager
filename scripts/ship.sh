#!/usr/bin/env bash
# Runs on your laptop. Staging gets origin/main; prod gets exactly the
# commit staging is running, so prod only ever ships what was tested.
set -Eeuo pipefail

HOST="${DEPLOY_HOST:-nba}"
BASE="/srv/fantasy-manager"

ENV="${1:-}"
case "$ENV" in
    staging)
        REF="origin/main"
        ;;
    prod)
        REF="$(ssh "$HOST" "git -C $BASE/staging rev-parse HEAD")"
        ;;
    *)
        echo "usage: ship.sh staging|prod" >&2
        exit 1
        ;;
esac

echo "Shipping $REF to $ENV on $HOST..."
ssh -t "$HOST" "set -e
    cd $BASE/$ENV
    git fetch --quiet origin
    git merge --ff-only $REF
    bash scripts/deploy.sh $ENV"
