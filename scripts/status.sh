#!/usr/bin/env bash
# scripts/status.sh — which instance (local dev vs Docker) is actually
# answering on echo's shared ports right now?
#
# Local Postgres/API/frontend and the Docker db/backend/frontend containers
# default to the exact same host ports (5432/8000/8501). Whichever binds
# first wins, silently — the other side gets no error, it's just unreachable.
# Run this any time a page or the database looks stale or wrong, BEFORE
# assuming the code is broken — you may be looking at the other instance.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_portcheck.sh

echo "echo — port status"
echo "-------------------"
for entry in "5432:postgres" "8000:backend" "8501:frontend"; do
  port="${entry%%:*}"
  role="${entry##*:}"
  read -r owner pid pname <<< "$(port_owner_detail "$port")"
  case "$owner" in
    free)     printf "  %-5s %-9s free\n" "$port" "$role" ;;
    docker)   printf "  %-5s %-9s DOCKER container (pid %s)\n" "$port" "$role" "$pid" ;;
    local)    printf "  %-5s %-9s LOCAL process: %s (pid %s)\n" "$port" "$role" "$pname" "$pid" ;;
    conflict) printf "  %-5s %-9s CONFLICT — both local AND docker are listening (pids %s: %s)\n" \
                     "$port" "$role" "$pid" "$pname" ;;
  esac
done

echo
echo "Docker containers for this project:"
docker compose ps 2>/dev/null || echo "  (docker not reachable / no containers up)"
