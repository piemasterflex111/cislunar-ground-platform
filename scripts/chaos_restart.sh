#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
service="${1:-telemetry-processor}"
echo "Restarting $service to verify recovery..."
docker compose --env-file .env -f deployment/compose.yaml kill "$service"
sleep 10
docker compose --env-file .env -f deployment/compose.yaml up -d "$service"
./scripts/smoke_test.sh
