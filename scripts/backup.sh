#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Emerald Rozalia - Project 1 : database and media backup
#
# Usage:
#   ./scripts/backup.sh              take a backup
#   ./scripts/backup.sh --check      verify prerequisites only, write nothing
#
# Environment (read from .env if not already set):
#   POSTGRES_DB, POSTGRES_USER      required
#   BACKUP_DIR                      default ./backups
#   BACKUP_RETENTION_DAYS           default 30, set 0 to keep everything
#   COMPOSE_DB_SERVICE              default db
#
# For production, point BACKUP_DIR at storage on a different host or volume.
# A backup that lives on the machine it protects is not a backup.
#
# Restore (destructive - confirm the target database first):
#   gunzip -c backups/db_<STAMP>.sql.gz \
#     | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
#   tar -xzf backups/media_<STAMP>.tar.gz
#
# The previous version of this script could not run: it referenced
# $POSTGRES_USER and $POSTGRES_DB, which live in .env and are never exported,
# so under `set -u` it aborted with "unbound variable". It also redirected
# pg_dump straight into the target file, so a failed dump left a truncated file
# that looked like a valid backup, and it hid tar failures behind
# `2>/dev/null || true`. All three are fixed here.
# ---------------------------------------------------------------------------
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

die() { printf 'backup: FAILED: %s\n' "$*" >&2; exit 1; }
log() { printf 'backup: %s\n' "$*"; }

# --- load .env without executing it ----------------------------------------
# Values already present in the environment win, so CI can override.
load_env() {
  local file="$1" line key val
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    [ "${line#*=}" = "$line" ] && continue
    key="${line%%=*}"; val="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    case "$val" in
      \"*\") val="${val#\"}"; val="${val%\"}" ;;
      \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    [ -z "${!key-}" ] && export "$key=$val"
  done < "$file"
}
load_env "$PROJECT_ROOT/.env"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
DB_SERVICE="${COMPOSE_DB_SERVICE:-db}"

# --- preconditions ----------------------------------------------------------
[ -n "${POSTGRES_DB:-}" ]   || die "POSTGRES_DB is not set (expected in .env or the environment)"
[ -n "${POSTGRES_USER:-}" ] || die "POSTGRES_USER is not set (expected in .env or the environment)"

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH"

docker info >/dev/null 2>&1 \
  || die "the Docker daemon is not reachable. Start Docker, then 'docker compose up -d'."

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$DB_SERVICE"; then
  die "compose service '$DB_SERVICE' is not running. Run 'docker compose up -d $DB_SERVICE' first."
fi

docker compose exec -T "$DB_SERVICE" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 \
  || die "Postgres is running but not accepting connections for database '$POSTGRES_DB'"

if [ "$CHECK_ONLY" -eq 1 ]; then
  log "prerequisites OK: docker up, service '$DB_SERVICE' running, database '$POSTGRES_DB' ready"
  log "check complete, nothing written"
  exit 0
fi

# --- take the backup --------------------------------------------------------
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
DB_FINAL="$BACKUP_DIR/db_${STAMP}.sql.gz"
MEDIA_FINAL="$BACKUP_DIR/media_${STAMP}.tar.gz"
MANIFEST="$BACKUP_DIR/manifest_${STAMP}.txt"

TMP_DIR="$(mktemp -d "$BACKUP_DIR/.tmp_${STAMP}_XXXXXX")"
# Remove the staging directory on any exit path so a failure never leaves a
# half-written file that a later restore could mistake for a good backup.
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

TMP_SQL="$TMP_DIR/db.sql"

log "dumping database '$POSTGRES_DB' as user '$POSTGRES_USER'"
if ! docker compose exec -T "$DB_SERVICE" \
       pg_dump --clean --if-exists --no-owner --no-privileges \
               -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "$TMP_SQL"; then
  die "pg_dump exited non-zero; no backup written"
fi

# Verify before publishing. A plain-format pg_dump always ends with this line,
# so its presence proves the dump ran to completion rather than being cut off.
[ -s "$TMP_SQL" ] || die "pg_dump produced an empty file; no backup written"
tail -n 5 "$TMP_SQL" | grep -q 'PostgreSQL database dump complete' \
  || die "dump is incomplete (missing completion marker); no backup written"

TABLE_COUNT="$(grep -c '^CREATE TABLE ' "$TMP_SQL" || true)"
log "dump verified: $(wc -c < "$TMP_SQL") bytes, ${TABLE_COUNT} CREATE TABLE statements"

gzip -9 "$TMP_SQL"
mv "$TMP_SQL.gz" "$DB_FINAL"
log "wrote $DB_FINAL"

# --- media ------------------------------------------------------------------
# Uploads are excluded from git on purpose, so this is their only copy.
MEDIA_STATUS="skipped (no media/ directory yet - nothing has been uploaded)"
if [ -d media ]; then
  if [ -z "$(find media -type f -print -quit 2>/dev/null)" ]; then
    MEDIA_STATUS="skipped (media/ exists but contains no files)"
  else
    log "archiving media/"
    tar -czf "$TMP_DIR/media.tar.gz" media \
      || die "tar failed while archiving media/; database dump was written but media was not"
    tar -tzf "$TMP_DIR/media.tar.gz" >/dev/null \
      || die "media archive is unreadable; refusing to publish it"
    mv "$TMP_DIR/media.tar.gz" "$MEDIA_FINAL"
    MEDIA_STATUS="$(basename "$MEDIA_FINAL") ($(find media -type f | wc -l) files)"
    log "wrote $MEDIA_FINAL"
  fi
fi
log "media: $MEDIA_STATUS"

# --- manifest ---------------------------------------------------------------
{
  echo "Emerald Rozalia - Project 1 backup"
  echo "taken_at    : $(date -Iseconds)"
  echo "host        : $(hostname)"
  echo "database    : $POSTGRES_DB"
  echo "db_user     : $POSTGRES_USER"
  echo "db_archive  : $(basename "$DB_FINAL")"
  echo "db_bytes    : $(wc -c < "$DB_FINAL")"
  echo "db_tables   : $TABLE_COUNT"
  echo "media       : $MEDIA_STATUS"
  echo "sha256      :"
  ( cd "$BACKUP_DIR" && sha256sum "$(basename "$DB_FINAL")" \
      $( [ -f "$MEDIA_FINAL" ] && basename "$MEDIA_FINAL" ) ) | sed 's/^/  /'
  echo
  echo "restore (DESTRUCTIVE - check the target database first):"
  echo "  gunzip -c $(basename "$DB_FINAL") | docker compose exec -T $DB_SERVICE psql -U $POSTGRES_USER -d $POSTGRES_DB"
  [ -f "$MEDIA_FINAL" ] && echo "  tar -xzf $(basename "$MEDIA_FINAL")"
} > "$MANIFEST"
log "wrote $MANIFEST"

# --- retention --------------------------------------------------------------
if [ "$RETENTION_DAYS" -gt 0 ] 2>/dev/null; then
  DELETED="$(find "$BACKUP_DIR" -maxdepth 1 -type f \
      \( -name 'db_*.sql.gz' -o -name 'media_*.tar.gz' -o -name 'manifest_*.txt' \) \
      -mtime +"$RETENTION_DAYS" -print -delete | wc -l)"
  [ "$DELETED" -gt 0 ] && log "retention: removed $DELETED file(s) older than ${RETENTION_DAYS} days"
fi

log "OK - backup complete"
