#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PROJECT="${COMPOSE_PROJECT_NAME:-nav-updater}"
VOLUME="${PROJECT}_app_data"
mkdir -p "$BACKUP_DIR"

docker compose exec -T db pg_dump -U "${POSTGRES_USER:-nav}" -d "${POSTGRES_DB:-nav}" > "$BACKUP_DIR/db-$STAMP.sql"
docker run --rm -v "$VOLUME:/data:ro" -v "$(cd "$BACKUP_DIR" && pwd):/backup" alpine:3.21 \
  tar -czf "/backup/files-$STAMP.tar.gz" -C /data .
printf 'backup complete: %s\n' "$BACKUP_DIR"
