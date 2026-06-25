#!/usr/bin/env bash
set -euo pipefail

MIN_MEM_AVAILABLE_KIB="${RIDER_MIN_MEM_AVAILABLE_KIB:-204800}"
MIN_ROOT_FREE_KIB="${RIDER_MIN_ROOT_FREE_KIB:-2097152}"
RUNNER_PATTERN="Runner.Listener|Runner.Worker|actions-runner"

fail() {
  echo "::error::$*" >&2
  exit 1
}

for i in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then
    echo "docker daemon ready after ${i}s"
    break
  fi
  if [ "$i" -eq 60 ]; then
    fail "docker daemon did not become ready"
  fi
  sleep 2
done

mem_available_kib="$(awk '/MemAvailable/ {print $2}' /proc/meminfo)"
if [ -z "$mem_available_kib" ] || [ "$mem_available_kib" -lt "$MIN_MEM_AVAILABLE_KIB" ]; then
  fail "low host memory before deploy: MemAvailable=${mem_available_kib:-unknown}KiB, required=${MIN_MEM_AVAILABLE_KIB}KiB"
fi

root_free_kib="$(df -Pk / | awk 'NR == 2 {print $4}')"
if [ -z "$root_free_kib" ] || [ "$root_free_kib" -lt "$MIN_ROOT_FREE_KIB" ]; then
  fail "low root disk space before deploy: free=${root_free_kib:-unknown}KiB, required=${MIN_ROOT_FREE_KIB}KiB"
fi

if pgrep -af "$RUNNER_PATTERN" >/tmp/rider-runner-processes.txt; then
  cat /tmp/rider-runner-processes.txt >&2
  fail "GitHub self-hosted runner process is running on production EC2"
fi

if systemctl list-units --type=service --state=running 'actions.runner*' --no-legend | grep -q .; then
  systemctl list-units --type=service --state=running 'actions.runner*' --no-legend >&2
  fail "GitHub self-hosted runner service is running on production EC2"
fi

echo "production deploy preflight ok: MemAvailable=${mem_available_kib}KiB root_free=${root_free_kib}KiB"
