"""Fast, deterministic checks that need no model: invented packages, secrets, AI leftovers."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

from .diff import FileDiff
from .models import Finding

# Import name -> distribution name, for the common cases where they differ.
PY_IMPORT_ALIASES = {
    "yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python", "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "dotenv": "python-dotenv",
    "jwt": "pyjwt", "google": "google-api-python-client", "attr": "attrs", "magic": "python-magic",
    "Crypto": "pycryptodome", "OpenSSL": "pyopenssl", "serial": "pyserial", "git": "gitpython",
    "jose": "python-jose", "multipart": "python-multipart", "docx": "python-docx",
    "pptx": "python-pptx", "fitz": "pymupdf", "zmq": "pyzmq", "psycopg2": "psycopg2-binary",
}
NODE_BUILTINS = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto",
    "dgram", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream",
    "string_decoder", "sys", "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm",
    "wasi", "worker_threads", "zlib", "test",
    "bun",  # Bun's own runtime module
}
NODE_LOCKFILES = ("bun.lock", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml")
PY_LOCKFILES = ("uv.lock", "poetry.lock", "pdm.lock")

PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w\.]+)\s+import|import\s+([\w\.]+(?:\s*,\s*[\w\.]+)*))")
JS_IMPORT_RE = re.compile(
    r"""(?:(?:import|export)\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]"""
)
# `bun:test`, `node:fs`, `npm:x`, `jsr:@std/x`, `virtual:`, URLs: provided by the runtime or bundler, not package.json.
JS_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:")
# Package names as lockfiles spell them: `"name@1.2.3"`, `name@^1:`, `/name@1.2.3:`, `"node_modules/name"`.
NODE_LOCK_NAME_RE = re.compile(
    r"""(?:^|[\s"'])/?((?:@[\w.-]+/)?[\w.-]+)@|node_modules/((?:@[\w.-]+/)?[\w.-]+)""", re.M
)
PY_LOCK_NAME_RE = re.compile(r'^name\s*=\s*"([^"]+)"', re.M)

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
    (re.compile(r"\bsk-(?:ant-|proj-|live_)?[A-Za-z0-9_\-]{20,}\b"), "API secret key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "Slack token"),
    (re.compile(r"""(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*['"][^'"\s]{12,}['"]"""),
     "hard-coded credential"),
]

# Markers a model leaves behind when it skips work or elides code.
# Each must start the line (after indentation), so text inside strings and docs does not count.
PLACEHOLDER_PATTERNS = [
    re.compile(r"(?i)^\s*(?:#|//|/\*|\*)\s*\.\.\.\s*(?:existing|rest of|remaining|other|previous)\b"),
    re.compile(r"(?i)^\s*(?:#|//)\s*(?:rest of (?:the )?(?:code|implementation|file)|existing code (?:here|unchanged))"),
    re.compile(r"(?i)^\s*(?:#|//)\s*(?:your|add(?: your)?|implement(?:ation)?|insert) (?:code|logic|implementation) here"),
    re.compile(r"(?i)^\s*(?:#|//)\s*TODO:?\s*implement\b"),
    re.compile(r"^\s*raise NotImplementedError\b"),
]
CODE_FILE_RE = re.compile(
    r"\.(?:py|pyi|[cm]?[jt]sx?|go|rs|java|kt|kts|scala|rb|php|cs|c|cc|cpp|h|hpp|swift|m|mm|sh|bash|lua|dart|vue|svelte)$"
)
DEBUG_PATTERNS = [
    (re.compile(r"^\s*debugger;?\s*$"), "debugger statement"),
    (re.compile(r"\b(?:breakpoint\(\)|pdb\.set_trace\(\)|ipdb\.set_trace\(\))"), "debugger breakpoint"),
]

TEST_PATH_RE = re.compile(r"(?:^|/)(?:tests?|__tests__|spec)/|(?:_test|\.test|\.spec|test_)[^/]*$")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _req_name(spec: str) -> str | None:
    spec = spec.split("#", 1)[0].strip()
    if not spec or spec.startswith(("-", "git+", "http://", "https://")):
        return None
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._\-]*)", spec)
    return _norm(m.group(1)) if m else None


def declared_python_deps(root: Path) -> set[str] | None:
    """Names declared in requirements files / pyproject, or None if the repo declares none."""
    names: set[str] = set()
    found_manifest = False
    for req in list(root.glob("requirements*.txt")) + list(root.glob("requirements/*.txt")):
        found_manifest = True
        for line in req.read_text(errors="ignore").splitlines():
            if n := _req_name(line):
                names.add(n)
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text())
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project", {})
        specs = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            specs += group
        for group in data.get("dependency-groups", {}).values():
            specs += [s for s in group if isinstance(s, str)]
        poetry = data.get("tool", {}).get("poetry", {})
        specs += list(poetry.get("dependencies", {}).keys())
        for grp in poetry.get("group", {}).values():
            specs += list(grp.get("dependencies", {}).keys())
        if specs or project:
            found_manifest = True
        for s in specs:
            if n := _req_name(s):
                names.add(n)
    for extra in ("setup.cfg", "setup.py", "Pipfile"):
        if (root / extra).exists():
            found_manifest = True
            for m in re.finditer(r"['\"]?([A-Za-z0-9][A-Za-z0-9._\-]*)\s*(?:[<>=~!]=|['\"]\s*[,\]=])", (root / extra).read_text(errors="ignore")):
                names.add(_norm(m.group(1)))
    return names if found_manifest else None


def local_python_modules(root: Path) -> set[str]:
    mods: set[str] = set()
    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if p.suffix == ".py":
                mods.add(p.stem)
            elif p.is_dir() and not p.name.startswith("."):
                mods.add(p.name)
    return mods


def declared_node_deps(root: Path) -> set[str] | None:
    pkg = root / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text())
    except json.JSONDecodeError:
        return None
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        names |= set((data.get(key) or {}).keys())
    return names


def locked_python_packages(root: Path) -> set[str]:
    """Every package a Python lockfile installs, including ones pulled in by other packages."""
    names: set[str] = set()
    for lock in PY_LOCKFILES:
        if (root / lock).exists():
            names |= {_norm(n) for n in PY_LOCK_NAME_RE.findall((root / lock).read_text(errors="ignore"))}
    pipfile_lock = root / "Pipfile.lock"
    if pipfile_lock.exists():
        try:
            data = json.loads(pipfile_lock.read_text())
        except json.JSONDecodeError:
            data = {}
        for section in ("default", "develop"):
            names |= {_norm(n) for n in (data.get(section) or {})}
    return names


def locked_node_packages(root: Path) -> tuple[set[str], str | None]:
    """Every package a Node lockfile installs, and that lockfile's name (None if there is none)."""
    names: set[str] = set()
    found = None
    for lock in NODE_LOCKFILES:
        if (root / lock).exists():
            found = found or lock
            for a, b in NODE_LOCK_NAME_RE.findall((root / lock).read_text(errors="ignore")):
                names.add(a or b)
    return names, found


def _undeclared(path: str, line: int, name: str, manifest: str, locked: bool, lockfile: str | None) -> Finding:
    if locked:
        return Finding(
            path=path, line=line, severity="medium", category="bug",
            title=f"`{name}` is imported but only installed indirectly",
            explanation=(
                f"`{name}` is installed today only because another package depends on it ({lockfile} has it). "
                "If that package drops it or changes its version, this import breaks. "
                f"Add `{name}` to {manifest} so the project depends on it on purpose."
            ),
            source="static",
        )
    return Finding(
        path=path, line=line, severity="high", category="invented",
        title=f"`{name}` is imported but not in {manifest}",
        explanation=(
            f"`{name}` is not built in, not part of this repo, and not declared in {manifest}. "
            + (f"{lockfile} does not install it either, so the package was most likely invented, or it was never "
               "added: " if lockfile else "Either the package was invented, or it was never added to the "
               "dependencies: ")
            + "a fresh install or CI build will fail on this import."
        ),
        source="static",
    )


def _python_imports(line: str) -> list[str]:
    m = PY_IMPORT_RE.match(line)
    if not m:
        return []
    if m.group(1):
        mod = m.group(1)
        return [] if mod.startswith(".") else [mod.split(".")[0]]
    return [part.strip().split(".")[0].split(" as ")[0].strip() for part in m.group(2).split(",")]


def check_imports(files: list[FileDiff], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    py_deps = declared_python_deps(root)
    py_local = local_python_modules(root)
    node_deps = declared_node_deps(root)
    py_locked = locked_python_packages(root) if py_deps is not None else set()
    py_lockfile = next((n for n in (*PY_LOCKFILES, "Pipfile.lock") if (root / n).exists()), None)
    node_locked, node_lockfile = locked_node_packages(root) if node_deps is not None else (set(), None)
    stdlib = set(sys.stdlib_module_names)

    for f in files:
        if f.status == "deleted":
            continue
        if f.path.endswith(".py") and py_deps is not None:
            for line_no, text in f.added_lines.items():
                for mod in _python_imports(text):
                    if not mod or mod in stdlib or mod in py_local or mod == "__future__":
                        continue
                    dist = _norm(PY_IMPORT_ALIASES.get(mod, mod))
                    if dist in py_deps or _norm(mod) in py_deps:
                        continue
                    locked = dist in py_locked or _norm(mod) in py_locked
                    findings.append(_undeclared(f.path, line_no, mod, "requirements or pyproject", locked, py_lockfile))
        if re.search(r"\.(?:[cm]?[jt]sx?)$", f.path) and node_deps is not None:
            for line_no, text in f.added_lines.items():
                for spec in JS_IMPORT_RE.findall(text):
                    if spec.startswith((".", "/", "~", "@/", "#")) or JS_SCHEME_RE.match(spec):
                        continue
                    parts = spec.split("/")
                    name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
                    if name in NODE_BUILTINS or name in node_deps or f"@types/{name}" in node_deps:
                        continue
                    findings.append(_undeclared(f.path, line_no, name, "package.json", name in node_locked, node_lockfile))
    return findings


def check_secrets(files: list[FileDiff]) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        is_test = bool(TEST_PATH_RE.search(f.path))
        for line_no, text in f.added_lines.items():
            for pattern, label in SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(Finding(
                        path=f.path, line=line_no, severity="low" if is_test else "high", category="security",
                        title=f"Possible {label} committed in {'a test' if is_test else 'code'}",
                        explanation=(
                            "Anything committed stays in git history even after it is deleted. Move it to an "
                            "environment variable or secret store, and rotate the credential if it is real."
                            + (" In a test this is often a fake value; if so, ignore this." if is_test else "")
                        ),
                        source="static",
                    ))
                    break
    return findings


def check_leftovers(files: list[FileDiff]) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if not CODE_FILE_RE.search(f.path):
            continue
        is_test = bool(TEST_PATH_RE.search(f.path))
        for line_no, text in f.added_lines.items():
            if any(p.search(text) for p in PLACEHOLDER_PATTERNS):
                findings.append(Finding(
                    path=f.path, line=line_no, severity="high", category="leftover",
                    title="Placeholder left where real code should be",
                    explanation=(
                        "This looks like a stand-in an AI assistant writes when it skips or elides code "
                        "(\"... existing code\", \"implement here\", NotImplementedError). Whatever was meant to be "
                        "here is missing, or real code was deleted and replaced by the comment."
                    ),
                    source="static",
                ))
                continue
            if is_test:
                continue
            for pattern, label in DEBUG_PATTERNS:
                if pattern.search(text):
                    findings.append(Finding(
                        path=f.path, line=line_no, severity="low", category="leftover",
                        title=f"{label[0].upper()}{label[1:]}",
                        explanation="Debug code that should not ship.",
                        # Offer one-click deletion only when the line holds nothing else.
                        suggestion="" if pattern.fullmatch(text.strip()) else None,
                        source="static",
                    ))
                    break
    return findings


def run_static_checks(files: list[FileDiff], root: Path) -> list[Finding]:
    return check_imports(files, root) + check_secrets(files) + check_leftovers(files)
