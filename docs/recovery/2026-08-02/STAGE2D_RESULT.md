# Stage 2D — Contradictory Cache-Wrapper Test Disposition

## 1. Branch and Git State

- **Branch**: `hermes/cislunar-phase1-recovery-20260802`
- **Current HEAD**: `ab3ee28 refactor: remove obsolete career-oriented demo script`
- **Status**: modified `tests/test_m1_cache_wrapper.py` (removed 1 contradictory assertion), no staging
- **No files staged or committed** (per acceptance criteria)

## 2. Evidence Traced

### 2.1 Origin of `tests/test_m1_cache_wrapper.py`

Confirmed: `tests/test_m1_cache_wrapper.py` is **untracked** by Git. git ls-files and git log show zero commits involving this file. The test file is not part of the canonical HEAD tree or any branch's tracked set. It was introduced externally (session artifact), not authored in repo history.

### 2.2 COMMS_SCHEDULE — authoritative mapping

Source: `mission_ground/common/config.py` lines 38–47 (committed in HEAD).

| Index | window_id | offset_minutes |
|---|---|---|
| 0 | 0 | -360 |
| 1 | 1 | 120 |
| 2 | 2 | -240 |
| 3 | 3 | 300 |
| 4 | 4 | -60 |
| 5 | 5 | 180 |
| 6 | 6 | -480 |
| 7 | 7 | 420 |

**At offset_minutes=120, the authoritative window_id is 1** (index 1 of the tuple).

### 2.3 Programme WINDOW-ID reference

No file within the repository provides an authoritative declaratioon that maps window_id 2 or 4 to offset 120.

### 2.4 Offset 120 Requirement

The offset 120 appears in two places and they agree:
1. `COMMS_SCHEDULE` defines `CommWindow(window_id=1, offset_minutes=120)`
2. `tests/test_m1_cache_wrapper.py` copies the same tuple as `SAMPLE_WINDOWS`

Both place 120 at index 1, window_id 1.

### 2.5 Contradictory assertions in the test file

**`test_window_by_offset_known`** (line 108–111):
```python
result = w.window(120)
assert result == CommWindow(window_id=1, offset_minutes=120)
```
Result: ✅ PASS

**`test_code_decoded`** (line 275–280):
```python
result = w.window(120)
assert result.window_id in (2, 4)
```
Result: ❌ FAIL — actual is 1, expected (2, 4)

Both assert on `w.window(120)`. The first says the result is window_id=1. The second says the result must be window_id 2 or 4. They are logically incompatible—they cannot both be true for the same input and the same production data.**

## 3. Documentary Check

No design document, issue file, recovery report, or architecture note in `docs/`, `.github/`, or root directory references window_id 2 or 4 for offset 120. The INTERFACE_CONTROL_DOCUMENT.md makes a generic reference to "schedule" but does not specify window IDs.

## 4. Disposition

**`test_code_decoded` is an obsolete, unsupported test expectation.**

1. The authoritative production mapping (COMMS_SCHEDULE) is window_id=1.
2. The same test file contains `test_window_by_offset_known` asserting window_id=1 for the same input—this test passes.
3. No external document, issue, or design spec asserts window_id 2 or 4.
4. The test file is itself untracked—its expectations were generated externally and are not a canonical requirement.

### 4.1 No production change

`COMMS_SCHEDULE` is **not** modified per acceptance criteria: "Do not change COMMS_SCHEDULE unless authoritative evidence proves window_id 1 is wrong." No such evidence exists.

## 5. Test correction

The contradiction is in the test, not in the production data. The fix is to remove only the unsupported assertion:

**Line 280**: Remove `assert result.window_id in (2, 4)  # type: ignore[arg-type]`

The valid checks that remain after removal:
- `assert result is not None` — still valid
- `assert result.offset_minutes == 120` — still valid

This is a test correction, not a weakening of coverage, because the assertion is demonstrably wrong (contradicts both COMMS_SCHEDULE and another test in the same file) and no external authoritative source supports it.

## 6. Full-suite result

- **Before fix**: 178 passed, 1 failed, 2 skipped
- **After fix**: 179 passed, 0 failed, 2 skipped
- **Skipped**: `test_phase2_ground_worker_helpers.py` (missing redis), `test_telemetry_storage.py` (missing sqlalchemy) — both pre-existing due to optional dependencies.

## 7. Git status recap

- **Branch**: `hermes/cislunar-phase1-recovery-20260802`
- **HEAD**: `ab3ee28`
- **Status**: working directory modified (test file only), no staging
