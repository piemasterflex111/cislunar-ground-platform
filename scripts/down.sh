#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

compose_args=(-f deployment/compose.yaml)
if [[ -f .env ]]; then
  compose_args=(--env-file .env "${compose_args[@]}")
fi

# Named volumes are intentionally preserved so raw evidence, processed results,
# queued work, and the simulator boot identifier survive an ordinary restart.
docker compose "${compose_args[@]}" down --remove-orphans
