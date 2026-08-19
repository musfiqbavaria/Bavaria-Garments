#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Emerald Rozalia - Project 1 : bring up the local stack
#
#   ./scripts/run_local.sh          build if needed, start, wait, report
#   ./scripts/run_local.sh --fresh  destroy the database and volumes first
#   ./scripts/run_local.sh --logs   follow the logs after starting
#   ./scripts/run_local.sh --down   stop everything, keep the data
#
# Starts postgres, redis, the web application, a celery worker, celery beat and
# nginx, then waits until the site actually answers before telling you it is up.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
die()  { printf '\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

FRESH=0
FOLLOW=0
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=1 ;;
    --logs)  FOLLOW=1 ;;
    --down)  log 'stopping the stack'; docker compose down; exit 0 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $arg" ;;
  esac
done

# --- preconditions ----------------------------------------------------------
command -v docker >/dev/null 2>&1 || die 'docker is not installed or not on PATH'

if ! docker info >/dev/null 2>&1; then
  die 'the Docker daemon is not reachable. Start Docker Desktop, wait for it to
       report Running, then run this again.'
fi

[ -f .env ] || die '.env is missing. Copy .env.example to .env and edit it.'

# The stack only works with the postgres engine: db and redis are compose
# services, and sqlite would leave each container with its own private file.
if grep -qE '^DB_ENGINE=(sqlite|sqlite3)' .env; then
  die 'DB_ENGINE is set to sqlite in .env. Set DB_ENGINE=postgres for the
       docker stack, or run without docker using manage.py directly.'
fi

for required in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
  grep -qE "^${required}=.+" .env || die "$required is not set in .env"
done

# --- optional reset ---------------------------------------------------------
if [ "$FRESH" -eq 1 ]; then
  warn 'destroying containers AND volumes: the database and all uploads go with them'
  docker compose down -v
fi

# --- build and start --------------------------------------------------------
log 'building images (first run pulls base images and compiles psycopg; expect a few minutes)'
docker compose build

log 'starting postgres, redis, web, worker, beat and nginx'
docker compose up -d

# --- wait for the site to answer -------------------------------------------
# compose reporting "Started" only means the processes launched. The entrypoint
# still has to wait for postgres, migrate, collect static and seed.
log 'waiting for the application to answer (migrations and first-run seed take a minute)'
ATTEMPTS=90
for i in $(seq 1 "$ATTEMPTS"); do
  code="$(docker compose exec -T web \
            python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/login/',timeout=3).status)" \
            2>/dev/null || true)"
  if [ "$code" = "200" ]; then
    printf '    application answering after %ss\n' "$((i * 2))"
    break
  fi
  if [ "$i" -eq "$ATTEMPTS" ]; then
    printf '\n'
    warn 'the application did not answer in time. Recent web logs:'
    docker compose logs --tail 40 web
    die 'startup did not complete. Fix the error above, then re-run.'
  fi
  sleep 2
done

# --- report -----------------------------------------------------------------
log 'service status'
docker compose ps

log 'ready'
cat <<'INFO'
    Site            http://localhost
    Django admin    http://localhost/admin/
    Sign in with DEFAULT_ADMIN_USERNAME / DEFAULT_ADMIN_PASSWORD from .env,
    then change that password.

    Useful commands
      docker compose logs -f web            follow the application log
      docker compose exec web bash          shell inside the container
      docker compose exec web python manage.py report_unscoped
      docker compose exec db psql -U emerald -d emerald_rozalia_project1
      ./scripts/run_local.sh --down         stop, keeping the data
      ./scripts/run_local.sh --fresh        start over from an empty database

    Note: /media/ returns 404 by design. Uploads are delivered through
    /files/<type>/<id>/<action>/ so they pass the permission check and are
    written to the audit log.
INFO

if [ "$FOLLOW" -eq 1 ]; then
  log 'following logs (Ctrl-C to stop; the stack keeps running)'
  docker compose logs -f
fi
