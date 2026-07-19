#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:?usage: scripts/restore.sh BACKUP_DIR}"
PROJECT="${COMPOSE_PROJECT_NAME:-nav-updater}"
VOLUME="${PROJECT}_app_data"
DB_FILE="$(ls -1t "$BACKUP_DIR"/db-*.sql | head -1)"
FILES_FILE="$(ls -1t "$BACKUP_DIR"/files-*.tar.gz | head -1)"

docker compose stop app worker
cat "$DB_FILE" | docker compose exec -T db psql -U "${POSTGRES_USER:-nav}" -d "${POSTGRES_DB:-nav}"
docker run --rm -v "$VOLUME:/data" -v "$(cd "$BACKUP_DIR" && pwd):/backup" alpine:3.21 \
  sh -c "rm -rf /data/* && tar -xzf /backup/$(basename "$FILES_FILE") -C /data"
docker compose start app worker
printf 'restore complete from %s\n' "$BACKUP_DIR"
