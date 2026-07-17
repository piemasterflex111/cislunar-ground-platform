#!/usr/bin/env python3
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manifest = tomllib.loads((ROOT / "requirements/requirements.toml").read_text())
errors: list[str] = []
seen: set[str] = set()

for requirement in manifest.get("requirement", []):
    rid = requirement["id"]
    if rid in seen:
        errors.append(f"duplicate requirement ID: {rid}")
    seen.add(rid)

    test_path, _, test_name = requirement["test"].partition("::")
    test_file = ROOT / test_path
    doc_file = ROOT / requirement["doc"]
    if not test_file.is_file():
        errors.append(f"{rid}: missing test file {test_path}")
    elif test_name and f"def {test_name}(" not in test_file.read_text():
        errors.append(f"{rid}: missing test function {test_name}")
    if not doc_file.is_file():
        errors.append(f"{rid}: missing document {requirement['doc']}")

if errors:
    print("TRACEABILITY FAILED")
    print("\n".join(f"- {error}" for error in errors))
    sys.exit(1)

print(f"TRACEABILITY PASS: {len(seen)} requirements mapped to tests and documents")
