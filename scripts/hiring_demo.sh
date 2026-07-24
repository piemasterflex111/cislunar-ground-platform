#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  cp .env.example .env
fi

GROUND_API_HOST_PORT="${HIRING_DEMO_GROUND_API_HOST_PORT:-8180}"
VENDOR_SIMULATOR_HOST_PORT="${HIRING_DEMO_VENDOR_SIMULATOR_HOST_PORT:-8181}"
OPERATOR_TOKEN="${OPERATOR_TOKEN:-local-phase2-operator-token}"
GROUND_API_URL="http://127.0.0.1:${GROUND_API_HOST_PORT}"
VENDOR_SIMULATOR_URL="http://127.0.0.1:${VENDOR_SIMULATOR_HOST_PORT}"
KEEP_RUNNING="${HIRING_DEMO_KEEP_RUNNING:-0}"
PROJECT_NAME="${HIRING_DEMO_PROJECT_NAME:-cislunar-hiring-proof}"
export GROUND_API_HOST_PORT VENDOR_SIMULATOR_HOST_PORT
compose=(docker compose --project-name "$PROJECT_NAME" --env-file .env -f deployment/compose.yaml)
started_here=1
work_dir="$(mktemp -d)"

cleanup() {
  rm -rf "$work_dir"
  if [[ "$started_here" == "1" && "$KEEP_RUNNING" != "1" ]]; then
    "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

wait_for_http() {
  local url="$1"
  for _ in $(seq 1 45); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

poll_json() {
  local url="$1"
  local output="$2"
  local status=""
  for _ in $(seq 1 30); do
    status="$(curl -sS \
      -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
      -o "$output" \
      -w '%{http_code}' \
      "$url" || true)"
    if [[ "$status" == "200" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

heading() {
  printf '\n%s\n' "$1"
  printf '%*s\n' "${#1}" '' | tr ' ' '='
}

heading "Secure Payload Telemetry Gateway — hiring proof"
echo "Proof path: raw preservation -> validation -> durable state -> queue -> worker result"

echo "Starting isolated stack '${PROJECT_NAME}' on ports ${GROUND_API_HOST_PORT}/${VENDOR_SIMULATOR_HOST_PORT}..."
compose_log="$work_dir/compose.log"
if ! "${compose[@]}" up -d --build --remove-orphans --wait --wait-timeout 120 \
  >"$compose_log" 2>&1; then
  cat "$compose_log" >&2
  "${compose[@]}" ps >&2
  "${compose[@]}" logs --tail=100 >&2
  fail "isolated stack did not start"
fi

wait_for_http "${GROUND_API_URL}/readyz" || fail "ground-api did not become ready"
wait_for_http "${VENDOR_SIMULATOR_URL}/healthz" || fail "vendor simulator did not become healthy"
echo "PASS  services ready"

heading "1. Nominal telemetry"
nominal_file="$work_dir/nominal.json"
curl -fsS \
  -X POST "${VENDOR_SIMULATOR_URL}/simulator/telemetry" \
  -H 'Content-Type: application/json' \
  -d '{
    "temperature_c": 28.4,
    "voltage_v": 28.1,
    "operating_mode": "SCIENCE",
    "fault_flags": 0,
    "failure_mode": "NONE"
  }' \
  -o "$nominal_file"

readarray -t nominal_values < <(python3 - "$nominal_file" <<'PY'
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["ground_status"] == 202, body
assert body["ground_response"]["status"] == "QUEUED", body
assert body["ground_response"]["duplicate"] is False, body
assert len(body["raw_hex"]) == 56, body
print(body["message_id"])
print(body["raw_hex"])
PY
)
message_id="${nominal_values[0]}"
raw_hex="${nominal_values[1]}"
echo "PASS  queued ${message_id} as one 28-byte frame"

raw_file="$work_dir/raw.json"
processed_file="$work_dir/processed.json"
poll_json "${GROUND_API_URL}/payloads/${message_id}/raw" "$raw_file" \
  || fail "raw record was not retrievable"
poll_json "${GROUND_API_URL}/payloads/${message_id}/processed" "$processed_file" \
  || fail "processed record was not retrievable"
python3 - "$raw_file" "$processed_file" "$message_id" "$raw_hex" <<'PY'
import json
import sys

raw = json.load(open(sys.argv[1], encoding="utf-8"))
processed = json.load(open(sys.argv[2], encoding="utf-8"))
message_id, submitted_hex = sys.argv[3], sys.argv[4]
assert raw["message_id"] == message_id, raw
assert raw["raw_hex"] == submitted_hex, raw
assert processed["message_id"] == message_id, processed
assert processed["classification"] == "NOMINAL", processed
PY
echo "PASS  exact raw bytes preserved before processing"
echo "PASS  worker stored NOMINAL engineering result"

heading "2. Exact duplicate"
duplicate_file="$work_dir/duplicate.json"
curl -fsS \
  -X POST "${VENDOR_SIMULATOR_URL}/simulator/telemetry" \
  -H 'Content-Type: application/json' \
  -d '{
    "temperature_c": 28.4,
    "voltage_v": 28.1,
    "operating_mode": "SCIENCE",
    "fault_flags": 0,
    "failure_mode": "DUPLICATE_LAST"
  }' \
  -o "$duplicate_file"
python3 - "$duplicate_file" "$message_id" "$raw_hex" <<'PY'
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["ground_status"] == 200, body
assert body["message_id"] == sys.argv[2], body
assert body["raw_hex"] == sys.argv[3], body
assert body["ground_response"]["status"] == "DUPLICATE", body
assert body["ground_response"]["duplicate"] is True, body
PY
echo "PASS  duplicate recognized without creating second logical work"

heading "3. Corrupted CRC"
corrupt_file="$work_dir/corrupt.json"
curl -fsS \
  -X POST "${VENDOR_SIMULATOR_URL}/simulator/telemetry" \
  -H 'Content-Type: application/json' \
  -d '{
    "temperature_c": 28.4,
    "voltage_v": 28.1,
    "operating_mode": "SCIENCE",
    "fault_flags": 0,
    "failure_mode": "CORRUPT_CRC"
  }' \
  -o "$corrupt_file"
python3 - "$corrupt_file" <<'PY'
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["ground_status"] == 422, body
assert body["ground_response"]["status"] == "REJECTED", body
assert body["ground_response"]["reason"] == "CRC_MISMATCH", body
PY
echo "PASS  corrupted frame rejected before processing"

heading "Verified result"
echo "PASS  nominal path"
echo "PASS  raw evidence preservation"
echo "PASS  idempotent duplicate handling"
echo "PASS  CRC failure rejection"
echo "PASS: hiring demonstration completed"

if [[ "$KEEP_RUNNING" == "1" ]]; then
  echo "Services remain running because HIRING_DEMO_KEEP_RUNNING=1."
else
  echo "Services started by this script will be stopped automatically."
fi
