"""Parse unified diffs into files, hunks and the line numbers a review can point at."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    status: str = "modified"  # added | deleted | modified | renamed
    patch: str = ""
    added_lines: dict[int, str] = field(default_factory=dict)  # new line number -> text
    # Every new-side line GitHub accepts an inline comment on (added + context).
    commentable_lines: set[int] = field(default_factory=set)
    is_binary: bool = False

    @property
    def additions(self) -> int:
        return len(self.added_lines)


def parse_unified_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    patch_lines: list[str] = []
    new_line = 0

    def finish() -> None:
        if current is not None:
            current.patch = "\n".join(patch_lines)
            files.append(current)

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            finish()
            patch_lines = []
            m = re.match(r"diff --git a/(.*) b/(.*)", raw)
            path = m.group(2) if m else raw.split()[-1]
            current = FileDiff(path=path, old_path=m.group(1) if m else None)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.status = "added"
        elif raw.startswith("deleted file mode"):
            current.status = "deleted"
        elif raw.startswith("rename from "):
            current.status = "renamed"
            current.old_path = raw[len("rename from "):]
        elif raw.startswith("rename to "):
            current.path = raw[len("rename to "):]
        elif raw.startswith("Binary files "):
            current.is_binary = True
        elif raw.startswith("--- ") or raw.startswith("+++ "):
            if raw.startswith("+++ ") and raw[4:] != "/dev/null":
                current.path = raw[4:].removeprefix("b/")
        elif raw.startswith("@@"):
            m = HUNK_RE.match(raw)
            if m:
                new_line = int(m.group(3))
            patch_lines.append(raw)
        elif raw.startswith("+"):
            current.added_lines[new_line] = raw[1:]
            current.commentable_lines.add(new_line)
            patch_lines.append(raw)
            new_line += 1
        elif raw.startswith("-"):
            patch_lines.append(raw)
        elif raw.startswith(" ") or raw == "":
            current.commentable_lines.add(new_line)
            patch_lines.append(raw)
            new_line += 1
        elif raw.startswith("\\"):
            patch_lines.append(raw)
    finish()
    return files


def numbered_patch(f: FileDiff) -> str:
    """The patch with new-side line numbers in the margin, so the model can cite lines."""
    out: list[str] = []
    new_line = 0
    for raw in f.patch.splitlines():
        if raw.startswith("@@"):
            m = HUNK_RE.match(raw)
            if m:
                new_line = int(m.group(3))
            out.append(raw)
        elif raw.startswith("+"):
            out.append(f"{new_line:>5} +{raw[1:]}")
            new_line += 1
        elif raw.startswith("-"):
            out.append(f"{'':>5} -{raw[1:]}")
        elif raw.startswith("\\"):
            out.append(raw)
        else:
            out.append(f"{new_line:>5}  {raw[1:] if raw else ''}")
            new_line += 1
    return "\n".join(out)
