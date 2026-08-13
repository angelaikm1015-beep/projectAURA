"""Generate frontend/js/options-data.js from options.py.

The dropdown lists have one source of truth (options.py), but the signup form
shouldn't go blank when the API is unreachable. So we bundle a generated copy
for the frontend to render from immediately.

Run after editing options.py:

    python backend/generate_options.py

backend/smoke_test.py fails if the generated file is out of date, so the two
can't quietly drift apart.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import options

OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "options-data.js"
)

HEADER = """\
/* GENERATED FILE — do not edit by hand.
 *
 * Source:     backend/options.py
 * Regenerate: python backend/generate_options.py
 *
 * Bundled so the signup dropdowns render instantly and still work when the Flask
 * API is unreachable. SignupPage refreshes from GET /api/options when it can, so
 * a running server always wins.
 */
"""


def render() -> str:
    payload = json.dumps(options.as_dict(), indent=2, ensure_ascii=False)
    return f"{HEADER}\nexport const OPTIONS = {payload};\n"


def is_current() -> bool:
    try:
        return OUTPUT_PATH.read_text(encoding="utf-8") == render()
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    OUTPUT_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
