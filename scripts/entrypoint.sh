#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Emerald Rozalia - Project 1 : container entrypoint
#
# Runs before the container's command. Without this, gunicorn started against an
# empty database and every request returned a 500 until somebody remembered to
# run migrate by hand.
#
# Controlled by two flags so a server rollout stays an explicit step:
#   DJANGO_AUTO_MIGRATE=1  wait for the database, then migrate and collectstatic
#   DJANGO_AUTO_SEED=1     seed the registry and demo data, first boot only
#
# On a server prefer DJANGO_AUTO_MIGRATE=0 with scripts/deploy.sh: concurrent web
# replicas would otherwise race each other's migrations.
# ---------------------------------------------------------------------------
set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*"; }

is_on() {
  case "${1:-0}" in 1|true|True|TRUE|yes|on) return 0 ;; *) return 1 ;; esac
}

DB_ENGINE_VALUE="$(printf '%s' "${DB_ENGINE:-postgres}" | tr '[:upper:]' '[:lower:]')"

# --- wait for Postgres ------------------------------------------------------
# compose healthchecks already gate startup, but a container can be restarted
# on its own, and a database that is accepting connections is not necessarily
# ready to serve. Poll Django's own connection so we test the real path.
wait_for_db() {
  case "$DB_ENGINE_VALUE" in
    sqlite|sqlite3) log "sqlite: no database to wait for"; return 0 ;;
  esac
  local attempts="${DB_WAIT_ATTEMPTS:-60}"
  local delay="${DB_WAIT_DELAY:-2}"
  log "waiting for postgres at ${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}"
  for i in $(seq 1 "$attempts"); do
    if python -c "
import django, os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings')
django.setup()
from django.db import connection
connection.ensure_connection()
" >/dev/null 2>&1; then
      log "postgres is ready (attempt $i)"
      return 0
    fi
    sleep "$delay"
  done
  log "FAILED: postgres was not reachable after $((attempts * delay))s"
  return 1
}

# --- schema -----------------------------------------------------------------
run_migrations() {
  # Refuse to start if the models have drifted from the committed migrations.
  # Migrations are generated and reviewed in development, never here.
  if ! python manage.py makemigrations --check --dry-run >/dev/null 2>&1; then
    log "FAILED: models have changes with no matching migration."
    log "        Run 'python manage.py makemigrations portal' locally, review it,"
    log "        and commit it. Migrations are never generated on a server."
    return 1
  fi
  log "applying migrations"
  python manage.py migrate --noinput
}

# --- first-boot seed --------------------------------------------------------
# Keyed on DashboardPage being empty, so a restart does not reseed. seed_project1
# is idempotent anyway, but this keeps startup quiet and fast.
needs_seed() {
  python -c "
import django, os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings')
django.setup()
from portal.models import DashboardPage
sys.exit(0 if DashboardPage.objects.count() == 0 else 1)
" >/dev/null 2>&1
}

main() {
  if is_on "${DJANGO_AUTO_MIGRATE:-0}"; then
    wait_for_db
    run_migrations
    log "collecting static files"
    python manage.py collectstatic --noinput >/dev/null
  else
    log "DJANGO_AUTO_MIGRATE is off; skipping migrate and collectstatic"
    wait_for_db
  fi

  if is_on "${DJANGO_AUTO_SEED:-0}"; then
    if needs_seed; then
      log "empty database: seeding registry, organisation tree and demo data"
      python manage.py seed_project1
    else
      log "already seeded; skipping"
    fi
  fi

  # Roles must exist as Django groups for the authorisation layer to resolve
  # them. Idempotent, and cheap enough to run on every boot so a role added to
  # data/roles.json takes effect without a manual step.
  if is_on "${DJANGO_AUTO_MIGRATE:-0}"; then
    python manage.py sync_roles --verbosity 0 || log "WARNING: sync_roles failed"
  fi

  log "starting: $*"
  exec "$@"
}

main "$@"
