#!/usr/bin/env bash
# Shared helper — who owns a given host port right now: the Docker Desktop
# proxy, a local process, both at once, or nobody? Sourced by status.sh and
# refresh-docker.sh so this logic exists in exactly one place. Not meant to be
# run directly.
#
# Docker Desktop's port-forwarding proxy on macOS doesn't always collide at
# the OS socket-bind level the way two plain processes would — it's been
# observed listening on the same port AT THE SAME TIME as a local process
# (e.g. Postgres), with client behavior then depending on which one actually
# answers a given connection. So this checks ALL listeners on a port, not
# just the first one lsof happens to list.

port_owner_detail() {
  # port_owner_detail <port> -> "<owner> <pid> <process-name>"
  # owner is one of: free | local | docker | conflict
  # for "conflict", pid/name are pid1,pid2/name1,name2 (comma-joined)
  local port="$1"
  local pids owners=() names=() pids_list=()
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u)
  if [ -z "$pids" ]; then
    echo "free - -"
    return
  fi

  local pid pname owner
  while read -r pid; do
    [ -z "$pid" ] && continue
    pname=$(ps -p "$pid" -o comm= 2>/dev/null || echo "?")
    case "$pname" in
      *docker*) owner="docker" ;;
      *) owner="local" ;;
    esac
    owners+=("$owner")
    names+=("$pname")
    pids_list+=("$pid")
  done <<< "$pids"

  local uniq_owners
  uniq_owners=$(printf "%s\n" "${owners[@]}" | sort -u)
  if [ "$(printf "%s\n" "$uniq_owners" | wc -l | tr -d ' ')" -gt 1 ]; then
    local pid_csv name_csv
    pid_csv=$(IFS=,; echo "${pids_list[*]}")
    name_csv=$(IFS=,; echo "${names[*]}")
    echo "conflict $pid_csv $name_csv"
  else
    echo "${owners[0]} ${pids_list[0]} ${names[0]}"
  fi
}

port_owner() {
  # port_owner <port> -> prints "docker" | "local" | "conflict" | "free"
  port_owner_detail "$1" | awk '{print $1}'
}
