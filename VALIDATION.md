# Validation Record

Generated and checked on 2026-07-15.

## Passed

- `python3 -m pytest -q`: 10 tests passed.
- `python3 scripts/verify_traceability.py`: 8 requirements mapped to existing tests and documents.
- `python3 scripts/perf_packet_codec.py --frames 50000 --minimum-fps 10000`: 346,214 codec frames/second in the generation sandbox.
- `python3 -m compileall -q mission_ground`: all Python modules compiled.
- Bash syntax validation passed for every script.
- YAML, JSON, and TOML syntax validation passed.

## Not executed in the generation sandbox

- Docker image build and Docker Compose end-to-end smoke test, because Docker is not installed in the sandbox.
- Kubernetes deployment against a live cluster.
- Ruff and mypy, because their pinned project dependencies were not installed in the sandbox.

The repository CI workflow executes the missing container, lint, typing, and integration checks on GitHub-hosted runners.
