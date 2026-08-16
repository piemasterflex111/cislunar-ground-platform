SHELL := /bin/bash
EVIDENCE_DIR := $(shell git rev-parse --git-path verification)

.PHONY: verify verify-core
verify:
	@evidence_dir="$(EVIDENCE_DIR)"; mkdir -p "$$evidence_dir" || exit 1; \
	log="$$evidence_dir/latest.log"; \
	head="$$(git rev-parse --verify HEAD 2>/dev/null || printf UNBORN)"; \
	worktree_sha="$$( { git diff --no-ext-diff --binary HEAD -- . 2>/dev/null || git diff --no-ext-diff --binary -- .; git ls-files --others --exclude-standard -z | while IFS= read -r -d '' f; do printf '%s\0' "$$f"; sha256sum -- "$$f"; done; } | sha256sum | awk '{print $$1}')"; \
	rc=0; $(MAKE) --no-print-directory verify-core >"$$log" 2>&1 || rc=$$?; \
	cat "$$log"; \
	result=FAIL; if [ "$$rc" -eq 0 ]; then result=PASS; fi; \
	ts="$$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	printf '{"repository":"%s","head":"%s","worktree_sha256":"%s","command":"make verify-core","result":"%s","exit_code":%s,"timestamp_utc":"%s","log":"%s"}\n' "$(notdir $(CURDIR))" "$$head" "$$worktree_sha" "$$result" "$$rc" "$$ts" "$$log" >"$$evidence_dir/latest.json" || exit 1; \
	exit "$$rc"

PYTHON ?= $(if $(wildcard .venv312/bin/python),.venv312/bin/python,python3)
RUFF ?= $(if $(wildcard .venv312/bin/ruff),.venv312/bin/ruff,ruff)
MYPY ?= $(if $(wildcard .venv312/bin/mypy),.venv312/bin/mypy,mypy)

.PHONY: test lint typecheck deps up down smoke verify-demo perf

verify-core: deps test
	$(PYTHON) scripts/verify_traceability.py

deps:
	$(PYTHON) -c 'import pytest, redis, sqlalchemy'

test:
	$(PYTHON) -m pytest -q

lint:
	$(RUFF) check .

typecheck:
	$(MYPY) mission_ground

up:
	./scripts/up.sh

down:
	./scripts/down.sh

smoke:
	./scripts/smoke_test.sh

verify-demo:
	bash scripts/verify_demo.sh

perf:
	$(PYTHON) scripts/perf_packet_codec.py --frames 100000
