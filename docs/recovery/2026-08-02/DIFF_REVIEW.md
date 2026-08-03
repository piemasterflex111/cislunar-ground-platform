# Diff Review — config.py Between Two Copies

## Two Versions Compared

| Version | Relative Path |
|---------|---------------|
| A (working directory, modified in session) | mission_ground/common/config.py |
| B (cislunal-ground-platform copy, original source) | cislunal-ground-platform/mission_ground/common/config.py |

## Summary of Differences

### 1. Import Placement
- A places `from typing import Any` at line 8 (top of file).
- B places `from typing import Any` at line 95 (after first class docstring).

### 2. Section Divider Comments
- A has explicit section divider comments; B does not.
- A includes an `on-call state` section header.

### 3. Docstring Markup
- A uses backtick inline code style.
- B uses RST-style double-backtick markup.

### 4. Unicode Dashes
- A uses ASCII hyphens consistently.
- B uses em-dashes and Unicode en-dashes.

### 5. Internal Structure Comments (B only)
B adds three section headers that A lacks:
- `# -- internals`
- `# -- public methods`
- `# -- read-only properties (wrappers)`

### 6. Formatting / Whitespace
- A has different line breaks in multi-line docstrings.
- B reflows some docstrings with RST emphasis markers.

## Conclusion

Neither version is objectively better. They represent editorial iterations:

- **A** = later session edit with rapid inline changes.
- **B** = earlier organized version with clear section headers.

Both must be reconciled in Stage 2.