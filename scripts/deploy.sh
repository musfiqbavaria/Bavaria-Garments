#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Emerald Rozalia - Project 1 : deploy
#
# Migrations are generated and reviewed in development and committed to the
# repository. They are never generated here. The previous version of this
# script ran `makemigrations portal` on the server at every deploy, which meant
# the schema was invented on the production box, was not version controlled,
# and could differ between environments - and a field rename would have become
# an unreviewed drop-and-create.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# -T: no TTY, so this script also works from cron and CI.
compose_run() { docker compose exec -T web "$@"; }

docker compose up -d --build

# Refuse to deploy if the models have drifted from the committed migrations.
# This is the guard that replaces the old makemigrations call: it detects the
# same situation but fails instead of silently inventing a migration.
echo "deploy: checking for un-migrated model changes"
compose_run python manage.py makemigrations --check --dry-run

compose_run python manage.py migrate --noinput
compose_run python manage.py seed_project1
compose_run python manage.py collectstatic --noinput
compose_run python manage.py check

docker compose ps
echo "deploy: OK"
