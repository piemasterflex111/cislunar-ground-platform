# Verification Contract

## Boundary
The telemetry gateway source, tests, traceability checks, and declared local deployment artifacts in this repository.

## Deterministic evidence
`make verify` runs the repository's declared checks and persists the complete check output in the Git metadata path `verification/latest.log`. It also writes `verification/latest.json` there with the pre-check Git HEAD, a SHA-256 of the pre-check working-tree content, the exact verification target, UTC timestamp, result, and exit code. These evidence files do not alter the working tree.

## Fail-closed rule
If required verification dependencies are unavailable, any repository test fails, or traceability validation fails, do not claim repository behavior is verified. Physical or deployed behavior requires separate integration evidence.

## Verify
```bash
make verify
```

A successful claim requires `make verify` to exit with status `0` and `verification/latest.json` to record `"result":"PASS"` for that run. Otherwise the result is **NOT VERIFIED**.
