.PHONY: test lint typecheck verify up down smoke hiring-demo perf

test:
	python3 -m pytest -q

lint:
	ruff check .

typecheck:
	mypy mission_ground

verify: test
	python3 scripts/verify_traceability.py

up:
	./scripts/up.sh

down:
	./scripts/down.sh

smoke:
	./scripts/smoke_test.sh

hiring-demo:
	./scripts/hiring_demo.sh

perf:
	python3 scripts/perf_packet_codec.py --frames 100000
