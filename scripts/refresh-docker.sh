#!/usr/bin/env bash
# scripts/refresh-docker.sh — one command for the sequence otherwise done by
# hand: regenerate the Docker seed from the local database, rebuild the
# images, then fully reset the container stack so both new code AND new data
# land. The container's Postgres volume only re-runs
# docker-entrypoint-initdb.d on an EMPTY volume, so this always wipes it
# (`down -v`) before restoring — that's the part that's easy to forget.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_portcheck.sh

echo "==> Checking who currently owns port 5432..."
owner=$(port_owner 5432)
if [ "$owner" != "local" ]; then
  echo "    port 5432 is '$owner', not cleanly your local Postgres." >&2
  echo "    pg_dump connects to 'localhost' either way, and which one actually answers isn't" >&2
  echo "    guaranteed here -- this has to be unambiguous before dumping." >&2
  case "$owner" in
    docker)
      echo "    run 'docker compose down' first, so pg_dump hits your local Postgres, then re-run this script." >&2 ;;
    conflict)
      echo "    both a local process AND the Docker container are listening on 5432 right now." >&2
      echo "    run 'docker compose down' first (Docker Desktop's proxy doesn't always collide with a" >&2
      echo "    plain bind the way you'd expect, so both can be up at once) then re-run this script." >&2 ;;
    *)
      echo "    start your local Postgres (the one 'python -m echo.db' / the app normally uses) and re-run." >&2 ;;
  esac
  exit 1
fi
echo "    ok — local Postgres owns 5432."

echo "==> Regenerating docker/seed/10_echo_seed.sql.gz from the local database..."
pg_dump -h localhost -U "$(whoami)" -d echo --no-owner --no-acl --exclude-table-data=embeddings \
  | gzip > docker/seed/10_echo_seed.sql.gz
echo "    $(du -h docker/seed/10_echo_seed.sql.gz | cut -f1) written."

echo "==> Rebuilding images..."
docker compose build

echo "==> Wiping the old volume and starting fresh..."
docker compose down -v
docker compose up -d

echo "==> Waiting for the backend to become healthy..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo "    up after ${i}s"
    break
  fi
  sleep 1
done
curl -s http://localhost:8000/health
echo
echo "==> Done. Frontend: http://localhost:8501"
