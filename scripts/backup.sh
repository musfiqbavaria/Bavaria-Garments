#!/usr/bin/env bash
set -euo pipefail
mkdir -p backups
STAMP=$(date +%Y%m%d_%H%M%S)
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "backups/db_${STAMP}.sql"
tar -czf "backups/media_${STAMP}.tar.gz" media 2>/dev/null || true
