#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
cd "$(dirname "${BASH_SOURCE[0]}")/.."

[[ -f .env ]] || cp .env.example .env

# The file contains local development values in KEY=value form. Exporting them
# lets this script honor customized host ports while Compose reads the same file.
set -a
source .env
set +a

GROUND_API_HOST_PORT="${GROUND_API_HOST_PORT:-8080}"
VENDOR_SIMULATOR_HOST_PORT="${VENDOR_SIMULATOR_HOST_PORT:-8081}"

compose=(docker compose --env-file .env -f deployment/compose.yaml)

if ! "${compose[@]}" up -d --build --remove-orphans --wait --wait-timeout 120; then
  "${compose[@]}" ps
  "${compose[@]}" logs --tail=100
  exit 1
fi

wait_for_http() {
  local service_name="$1"
  local url="$2"

  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "PASS: ${service_name} is ready at ${url}"
      return 0
    fi
    sleep 2
  done

  echo "FAIL: ${service_name} did not become ready at ${url}" >&2
  return 1
}

if wait_for_http "ground-api" "http://127.0.0.1:${GROUND_API_HOST_PORT}/readyz" \
  && wait_for_http "vendor-simulator" "http://127.0.0.1:${VENDOR_SIMULATOR_HOST_PORT}/healthz"; then
  echo "Phase 2 telemetry path is ready."
  exit 0
fi

"${compose[@]}" ps
"${compose[@]}" logs --tail=100
exit 1
