#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${RIDER_REPO_DIR:-/opt/rider-server/repo}"
MIN_MEM_AVAILABLE_KIB="${RIDER_MIN_MEM_AVAILABLE_KIB:-204800}"
MIN_ROOT_FREE_KIB="${RIDER_MIN_ROOT_FREE_KIB:-2097152}"
OOM_SINCE="${RIDER_OOM_SINCE:-24 hours ago}"
RUNNER_PATTERN="Runner.Listener|Runner.Worker|actions-runner"

fail() {
  echo "::error::$*" >&2
  exit 1
}

cd "$REPO_DIR"

curl -fsS http://127.0.0.1:8000/health | grep -q '"status":"ok"' || fail "backend /health is not ok"

compose=(docker compose --env-file .env -p rider -f deploy/docker-compose.yml -f deploy/docker-compose.dev-public-admin.yml)
"${compose[@]}" ps

for service in db backend-api scheduler queue-recovery telegram-dispatch; do
  container_id="$("${compose[@]}" ps -q "$service")"
  [ -n "$container_id" ] || fail "missing compose service: $service"

  status="$(docker inspect -f '{{.State.Status}}' "$container_id")"
  [ "$status" = "running" ] || fail "$service is not running: $status"

  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
  case "$health" in
    healthy|none) ;;
    *) fail "$service health is $health" ;;
  esac
done

mem_available_kib="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
if [ -z "$mem_available_kib" ] || [ "$mem_available_kib" -lt "$MIN_MEM_AVAILABLE_KIB" ]; then
  fail "low host memory: MemAvailable=${mem_available_kib:-unknown}KiB, required=${MIN_MEM_AVAILABLE_KIB}KiB"
fi

root_free_kib="$(df -Pk / | awk 'NR == 2 {print $4}')"
if [ -z "$root_free_kib" ] || [ "$root_free_kib" -lt "$MIN_ROOT_FREE_KIB" ]; then
  fail "low root disk space: free=${root_free_kib:-unknown}KiB, required=${MIN_ROOT_FREE_KIB}KiB"
fi

if pgrep -af "$RUNNER_PATTERN" >/tmp/rider-runner-processes.txt; then
  cat /tmp/rider-runner-processes.txt >&2
  fail "GitHub self-hosted runner process is running on production EC2"
fi

if journalctl -k --since "$OOM_SINCE" | grep -Eiq 'out of memory|oom|killed process'; then
  journalctl -k --since "$OOM_SINCE" | grep -Ei 'out of memory|oom|killed process' >&2
  fail "kernel OOM signal found since $OOM_SINCE"
fi

echo "production health ok: MemAvailable=${mem_available_kib}KiB root_free=${root_free_kib}KiB"
