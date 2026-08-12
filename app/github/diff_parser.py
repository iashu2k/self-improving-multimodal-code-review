# app/github/diff_parser.py
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

  @property
  def right_side_lines(self) -> set[int]:
    """All new-side lines present in the diff (added + context).

    GitHub allows review comments on any of these; added lines are the
    preferred anchor, context lines are the legal fallback for findings
    about deleted code (which has no added-line anchor).
    """
    return {
      line.new_lineno
      for hunk in self.hunks
      for line in hunk.lines
      if line.kind in ("add", "context") and line.new_lineno is not None
    }


def reviewable_files(files: list[ChangedFile]) -> list[ChangedFile]:
  """Files a reviewer can meaningfully comment on.

  Deleted files have no RIGHT-side lines, so no comment can ever anchor
  to them; including them in prompts is pure noise. Filter at the render
  layer so every caller (worker, local CLI, enrichment) gets it for free.
  """
  return [f for f in files if f.status != "deleted"]


def parse_unified_diff(diff_text: str) -> list[ChangedFile]:
  files: list[ChangedFile] = []
  current_file: ChangedFile | None = None
  current_hunk: DiffHunk | None = None
  # File-header metadata ("new file mode", "deleted file mode",
  # "rename from") appears BEFORE "+++", while no ChangedFile exists yet.
  # Stash it here; the "+++" handler consumes it.
  pending_status = "modified"
  pending_old_path: str | None = None
  old_lineno = new_lineno = 0

  for raw in diff_text.splitlines():
    if raw.startswith("diff --git"):
      current_file = None
      current_hunk = None
      pending_status = "modified"
      pending_old_path = None
      continue

    if raw.startswith("new file mode"):
      pending_status = "added"
      continue
    if raw.startswith("deleted file mode"):
      pending_status = "deleted"
      continue
    if raw.startswith("rename from "):
      pending_old_path = raw.removeprefix("rename from ").strip()
      pending_status = "renamed"
      continue
    if raw.startswith("copy from "):
      pending_old_path = raw.removeprefix("copy from ").strip()
      continue

    if raw.startswith("\\"):
      # "\ No newline at end of file" marker: metadata, not a file line.
      # Must not consume a line number on either side.
      continue

    if raw.startswith("--- "):
      continue
    if raw.startswith("+++ "):
      path = raw[4:].strip()
      path = path.removeprefix("b/") if path != "/dev/null" else ""
      current_file = ChangedFile(path=path, old_path=pending_old_path, status=pending_status)
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
