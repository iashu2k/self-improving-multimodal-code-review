import ast
from dataclasses import dataclass

MAX_CHUNK_LINES = 120
OVERLAP_LINES = 10


@dataclass
class Chunk:
  file_path: str
  chunk_type: str  # code | test | doc | config
  symbol: str | None
  start_line: int
  end_line: int
  content: str


def classify_file(path: str) -> str:
  name = path.rsplit("/", 1)[-1].lower()
  if name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}":
    return "test"
  if name in {"readme.md", "readme", "contributing.md"} or name.endswith((".md", ".rst")):
    return "doc"
  if name in {"pyproject.toml", "setup.cfg", ".flake8", "ruff.toml"}:
    return "config"
  return "code"


def chunk_python_file(path: str, source: str) -> list[Chunk]:
  """Split a Python file into symbol-level chunks via AST.

  Each top-level function/class becomes a chunk with the file's imports
  prepended. Oversized symbols are split at line boundaries with overlap.
  """
  chunk_type = classify_file(path)
  lines = source.splitlines()

  try:
    tree = ast.parse(source)
  except SyntaxError:
    return [Chunk(path, chunk_type, None, 1, len(lines), source)]

  import_lines = [
    lines[node.lineno - 1]
    for node in ast.walk(tree)
    if isinstance(node, ast.Import | ast.ImportFrom) and node.lineno <= len(lines)
  ]
  import_header = "\n".join(dict.fromkeys(import_lines))  # dedupe, preserve order

  chunks: list[Chunk] = []
  covered: set[int] = set()

  for node in tree.body:
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
      continue

    start, end = node.lineno, node.end_lineno or node.lineno
    covered.update(range(start, end + 1))
    symbol_source = "\n".join(lines[start - 1 : end])

    if end - start + 1 <= MAX_CHUNK_LINES:
      content = f"{import_header}\n\n{symbol_source}" if import_header else symbol_source
      chunks.append(Chunk(path, chunk_type, node.name, start, end, content))
    else:
      # Split oversized symbols with overlap
      step = MAX_CHUNK_LINES - OVERLAP_LINES
      for i, chunk_start in enumerate(range(start, end + 1, step)):
        chunk_end = min(chunk_start + MAX_CHUNK_LINES - 1, end)
        piece = "\n".join(lines[chunk_start - 1 : chunk_end])
        content = (
          f"{import_header}\n\n# {node.name} (part {i + 1})\n{piece}"
          if import_header
          else f"# {node.name} (part {i + 1})\n{piece}"
        )
        chunks.append(
          Chunk(
            path,
            chunk_type,
            f"{node.name}#part{i + 1}",
            chunk_start,
            chunk_end,
            content,
          )
        )
        if chunk_end >= end:
          break

  # Module-level code not inside any symbol (constants, scripts)
  uncovered = [
    (i + 1, line)
    for i, line in enumerate(lines)
    if i + 1 not in covered and line.strip() and not line.strip().startswith(("import ", "from "))
  ]
  if uncovered:
    module_content = "\n".join(line for _, line in uncovered[:MAX_CHUNK_LINES])
    chunks.append(
      Chunk(path, chunk_type, "__module__", uncovered[0][0], uncovered[-1][0], module_content)
    )

  return chunks


def chunk_file(path: str, source: str) -> list[Chunk]:
  chunk_type = classify_file(path)
  if path.endswith(".py"):
    return chunk_python_file(path, source)
  # Non-Python files: whole-file chunk if small, else skip
  lines = source.splitlines()
  if len(lines) <= MAX_CHUNK_LINES:
    return [Chunk(path, chunk_type, None, 1, len(lines), source)]
  return []
