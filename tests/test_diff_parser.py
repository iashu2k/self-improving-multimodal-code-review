from app.github.diff_parser import parse_unified_diff

SAMPLE_DIFF = """diff --git a/app/auth.py b/app/auth.py
--- a/app/auth.py
+++ b/app/auth.py
@@ -10,3 +10,5 @@ def login(user):
     user = normalize(user)
-    return check(user)
+    if user.is_admin:
+        return True
+    return check(user)
"""


def test_parse_single_file_hunk() -> None:
  files = parse_unified_diff(SAMPLE_DIFF)

  assert len(files) == 1
  assert files[0].path == "app/auth.py"
  assert files[0].status == "modified"
  assert files[0].commentable_lines == {11, 12, 13}


def test_commentable_lines_excludes_context_and_deletions() -> None:
  files = parse_unified_diff(SAMPLE_DIFF)

  all_new = {
    line.new_lineno for hunk in files[0].hunks for line in hunk.lines if line.new_lineno is not None
  }

  assert all_new == {10, 11, 12, 13}


DIFF_WITH_NO_NEWLINE_MARKER = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@
 def divide(a: float, b: float) -> float:
-    return a / b
\\ No newline at end of file
+    result = a / b
+    return int(result)
"""


def test_no_newline_marker_does_not_shift_line_numbers() -> None:
  files = parse_unified_diff(DIFF_WITH_NO_NEWLINE_MARKER)

  assert files[0].commentable_lines == {2, 3}

  last_line = files[0].hunks[0].lines[-1]
  assert last_line.kind == "add"
  assert last_line.content == "    return int(result)"
  assert last_line.new_lineno == 3


def test_file_status_and_rename_tracking():
  diff = (
    "diff --git a/new.py b/new.py\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/new.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+line1\n"
    "+line2\n"
    "diff --git a/old.py b/old.py\n"
    "deleted file mode 100644\n"
    "index 2222222..0000000\n"
    "--- a/old.py\n"
    "+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n"
    "-gone1\n"
    "-gone2\n"
    "diff --git a/before.py b/after.py\n"
    "similarity index 90%\n"
    "rename from before.py\n"
    "rename to after.py\n"
    "index 3333333..4444444 100644\n"
    "--- a/before.py\n"
    "+++ b/after.py\n"
    "@@ -1,1 +1,1 @@\n"
    "-x = 1\n"
    "+x = 2\n"
  )
  files = parse_unified_diff(diff)
  assert files[0].status == "added" and files[0].path == "new.py"
  assert files[0].commentable_lines == {1, 2}
  assert files[1].status == "deleted" and files[1].path == ""
  assert files[2].status == "renamed"
  assert files[2].old_path == "before.py" and files[2].path == "after.py"
