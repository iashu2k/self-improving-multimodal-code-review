import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\s*(.*)$")


@dataclass
class DiffLine:
    kind: str  # "add" | "del" | "context"
    content: str
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class ChangedFile:
    path: str
    old_path: str | None = None
    status: str = "modified"  # added | modified | deleted | renamed
    hunks: list[DiffHunk] = field(default_factory=list)

    @property
    def commentable_lines(self) -> set[int]:
        return {
            line.new_lineno
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind == "add" and line.new_lineno is not None
        }


def parse_unified_diff(diff_text: str) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    current_file: ChangedFile | None = None
    current_hunk: DiffHunk | None = None
    old_lineno = new_lineno = 0

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            current_file = None
            current_hunk = None
            continue

        if raw.startswith("new file mode") and current_file is not None:
            current_file.status = "added"
            continue
        if raw.startswith("deleted file mode") and current_file is not None:
            current_file.status = "deleted"
            continue
        if raw.startswith("rename from ") and current_file is not None:
            current_file.old_path = raw.removeprefix("rename from ").strip()
            current_file.status = "renamed"
            continue

        if raw.startswith("--- "):
            continue
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            path = path.removeprefix("b/") if path != "/dev/null" else ""
            old_path = current_file.old_path if current_file else None
            current_file = ChangedFile(path=path, old_path=old_path)
            files.append(current_file)
            continue

        match = HUNK_RE.match(raw)
        if match and current_file is not None:
            old_start, old_count, new_start, new_count, section = match.groups()
            old_lineno, new_lineno = int(old_start), int(new_start)
            current_hunk = DiffHunk(
                old_start=int(old_start),
                old_count=int(old_count or 1),
                new_start=int(new_start),
                new_count=int(new_count or 1),
                section=section,
            )
            current_file.hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if raw.startswith("+"):
            current_hunk.lines.append(DiffLine("add", raw[1:], None, new_lineno))
            new_lineno += 1
        elif raw.startswith("-"):
            current_hunk.lines.append(DiffLine("del", raw[1:], old_lineno, None))
            old_lineno += 1
        else:
            content = raw[1:] if raw.startswith(" ") else raw
            current_hunk.lines.append(DiffLine("context", content, old_lineno, new_lineno))
            old_lineno += 1
            new_lineno += 1

    return files
