"""Static check of the React source: do all imports resolve, and do the names exist?

A stopgap for environments without Node. It catches the errors most likely to
break a build — a path that doesn't exist, a named import that isn't exported, a
package used but missing from package.json — but it does NOT typecheck, parse
JSX, or run anything. Once `npm run build` works, that supersedes this.

    python check_frontend_imports.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "frontend" / "src"
PKG = json.loads((ROOT / "frontend" / "package.json").read_text())
DEPS = set(PKG.get("dependencies", {})) | set(PKG.get("devDependencies", {}))

IMPORT_RE = re.compile(
    r'import\s+(?:(?P<clause>[^"\';]+?)\s+from\s+)?["\'](?P<path>[^"\']+)["\']', re.S)

problems = []
files = sorted(SRC.rglob("*.js")) + sorted(SRC.rglob("*.jsx"))


def resolve(importer: Path, spec: str):
    base = (importer.parent / spec).resolve()
    for cand in (base, base.with_suffix(".js"), base.with_suffix(".jsx"),
                 base / "index.js", base / "index.jsx"):
        if cand.is_file():
            return cand
    return None


def exports_of(path: Path) -> set:
    text = path.read_text()
    names = set()
    names |= set(re.findall(r'export\s+(?:async\s+)?function\s+(\w+)', text))
    names |= set(re.findall(r'export\s+(?:const|let|var|class)\s+(\w+)', text))
    for block in re.findall(r'export\s*\{([^}]*)\}', text):
        for piece in block.split(","):
            piece = piece.strip()
            if not piece:
                continue
            names.add(piece.split(" as ")[-1].strip())
    if re.search(r'export\s+default', text):
        names.add("default")
    return names


def named_imports(clause: str) -> tuple:
    """Return (default_name_or_None, [named...]) from an import clause."""
    clause = clause.strip()
    named = []
    match = re.search(r'\{([^}]*)\}', clause)
    if match:
        for piece in match.group(1).split(","):
            piece = piece.strip()
            if piece:
                named.append(piece.split(" as ")[0].strip())
        clause = clause[: match.start()].rstrip().rstrip(",")
    default = clause.strip() or None
    if default and (default.startswith("*") or not re.fullmatch(r'\w+', default)):
        default = None
    return default, named


for path in files:
    text = path.read_text()
    for m in IMPORT_RE.finditer(text):
        spec = m.group("path")
        clause = m.group("clause")
        rel = path.relative_to(ROOT)

        if spec.startswith("."):
            target = resolve(path, spec)
            if target is None:
                problems.append(f"{rel}: cannot resolve import '{spec}'")
                continue
            if clause:
                default, names = named_imports(clause)
                available = exports_of(target)
                if default and "default" not in available:
                    problems.append(
                        f"{rel}: imports default from '{spec}' but it has no default export")
                for name in names:
                    if name not in available:
                        problems.append(
                            f"{rel}: imports {{{name}}} from '{spec}' — not exported there")
        elif spec.startswith("/") or spec.startswith("virtual:"):
            continue
        else:
            pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
            if pkg not in DEPS:
                problems.append(f"{rel}: imports '{spec}' but '{pkg}' is not in package.json")

# CSS imports referenced from JS must exist too.
for path in files:
    for spec in re.findall(r'import\s+["\'](\.[^"\']+\.css)["\']', path.read_text()):
        if not (path.parent / spec).resolve().is_file():
            problems.append(f"{path.relative_to(ROOT)}: missing CSS '{spec}'")

# The Vite entry must exist and be referenced by index.html.
html = (ROOT / "frontend" / "index.html").read_text()
if "/src/main.jsx" not in html:
    problems.append("frontend/index.html does not reference /src/main.jsx")
if not (ROOT / "frontend" / "src" / "main.jsx").is_file():
    problems.append("frontend/src/main.jsx is missing")
if 'id="root"' not in html:
    problems.append("frontend/index.html has no #root element")

print(f"scanned {len(files)} source files")
for p in problems:
    print("  FAIL", p)
print("\nno import problems found" if not problems else f"\n{len(problems)} problem(s)")
sys.exit(1 if problems else 0)
