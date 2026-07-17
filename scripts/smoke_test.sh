#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -f .env ]]; then
  set -a
  # .env is an optional local runtime file.
  # shellcheck disable=SC1091
  source .env
  set +a
fi

GROUND_API_HOST_PORT="${GROUND_API_HOST_PORT:-8080}"
VENDOR_SIMULATOR_HOST_PORT="${VENDOR_SIMULATOR_HOST_PORT:-8081}"
OPERATOR_TOKEN="${OPERATOR_TOKEN:-local-phase2-operator-token}"
GROUND_API_URL="http://127.0.0.1:${GROUND_API_HOST_PORT}"
VENDOR_SIMULATOR_URL="http://127.0.0.1:${VENDOR_SIMULATOR_HOST_PORT}"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

wait_for_http() {
  local description="$1"
  local url="$2"
  local output_file="$3"

  for _ in $(seq 1 30); do
    if curl -fsS "$url" -o "$output_file"; then
      echo "PASS: ${description}"
      return 0
    fi
    sleep 1
  done
  fail "${description} did not succeed at ${url}"
}

poll_operator_record() {
  local description="$1"
  local url="$2"
  local output_file="$3"
  local http_status=""

  for _ in $(seq 1 30); do
    if http_status="$(curl -sS \
      -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
      -o "$output_file" \
      -w '%{http_code}' \
      "$url")" && [[ "$http_status" == "200" ]]; then
      echo "PASS: ${description}"
      return 0
    fi
    sleep 1
  done

  [[ ! -s "$output_file" ]] || python3 -m json.tool "$output_file" >&2 || true
  fail "${description} did not return HTTP 200; last status=${http_status:-connection-failed}"
}

wait_for_http \
  "ground-api readiness" \
  "${GROUND_API_URL}/readyz" \
  "${work_dir}/ready.json"
wait_for_http \
  "vendor-simulator health" \
  "${VENDOR_SIMULATOR_URL}/healthz" \
  "${work_dir}/vendor-health.json"

nominal_file="${work_dir}/nominal.json"
if ! nominal_http_status="$(curl -sS \
  -X POST "${VENDOR_SIMULATOR_URL}/simulator/telemetry" \
  -H 'Content-Type: application/json' \
  -d '{
    "temperature_c": 28.4,
    "voltage_v": 28.1,
    "operating_mode": "SCIENCE",
    "fault_flags": 0,
    "failure_mode": "NONE"
  }' \
  -o "$nominal_file" \
  -w '%{http_code}')"; then
  fail "nominal simulator request could not connect"
fi
[[ "$nominal_http_status" == "200" ]] \
  || fail "nominal simulator control returned HTTP ${nominal_http_status}"

if ! message_id="$(python3 -c '
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
message_id = body.get("message_id")
raw_hex = body.get("raw_hex")
assert body.get("ground_status") == 202, body
assert body.get("ground_response", {}).get("status") == "QUEUED", body
assert body.get("ground_response", {}).get("duplicate") is False, body
assert isinstance(message_id, str) and message_id, body
assert isinstance(raw_hex, str) and len(raw_hex) == 56, body
print(message_id)
' "$nominal_file")"; then
  python3 -m json.tool "$nominal_file" >&2 || true
  fail "nominal simulator response did not prove a newly queued 28-byte frame"
fi
echo "PASS: fresh nominal telemetry queued as message_id=${message_id}"

raw_file="${work_dir}/raw.json"
processed_file="${work_dir}/processed.json"
poll_operator_record \
  "raw telemetry retrieval" \
  "${GROUND_API_URL}/payloads/${message_id}/raw" \
  "$raw_file"
poll_operator_record \
  "processed telemetry retrieval" \
  "${GROUND_API_URL}/payloads/${message_id}/processed" \
  "$processed_file"

if ! python3 -c '
import json
import sys

raw = json.load(open(sys.argv[1], encoding="utf-8"))
submitted = json.load(open(sys.argv[2], encoding="utf-8"))
assert raw.get("message_id") == sys.argv[3], raw
assert raw.get("raw_hex") == submitted.get("raw_hex"), (raw, submitted)
' "$raw_file" "$nominal_file" "$message_id"; then
  fail "retrieved raw bytes do not match the submitted vendor bytes"
fi
echo "PASS: raw byte-for-byte preservation"

if ! python3 -c '
import json
import sys

processed = json.load(open(sys.argv[1], encoding="utf-8"))
assert processed.get("message_id") == sys.argv[2], processed
assert processed.get("classification") == "NOMINAL", processed
' "$processed_file" "$message_id"; then
  fail "processed result is missing the expected NOMINAL classification"
fi
echo "PASS: worker stored NOMINAL classification"

corrupt_file="${work_dir}/corrupt.json"
if ! corrupt_http_status="$(curl -sS \
  -X POST "${VENDOR_SIMULATOR_URL}/simulator/telemetry" \
  -H 'Content-Type: application/json' \
  -d '{
    "temperature_c": 28.4,
    "voltage_v": 28.1,
    "operating_mode": "SCIENCE",
    "fault_flags": 0,
    "failure_mode": "CORRUPT_CRC"
  }' \
  -o "$corrupt_file" \
  -w '%{http_code}')"; then
  fail "corrupt-frame simulator request could not connect"
fi
[[ "$corrupt_http_status" == "200" ]] \
  || fail "corrupt-frame simulator control returned HTTP ${corrupt_http_status}"

if ! python3 -c '
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body.get("ground_status") == 422, body
assert body.get("ground_response", {}).get("reason") == "CRC_MISMATCH", body
' "$corrupt_file"; then
  python3 -m json.tool "$corrupt_file" >&2 || true
  fail "corrupted telemetry was not rejected with CRC_MISMATCH"
fi
echo "PASS: corrupted telemetry rejected with CRC_MISMATCH"

echo "PASS: Phase 2 telemetry smoke proof"
