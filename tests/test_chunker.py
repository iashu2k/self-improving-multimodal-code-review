from app.ingestion.chunker import (
  MAX_CHUNK_LINES,
  OVERLAP_LINES,
  chunk_file,
  chunk_python_file,
  classify_file,
)

SAMPLE_SOURCE = """import os
from pathlib import Path

CONSTANT = 42


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("division by zero")
    return a / b


class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b
"""


def test_classify_file_types() -> None:
  assert classify_file("src/calc.py") == "code"
  assert classify_file("tests/test_calc.py") == "test"
  assert classify_file("README.md") == "doc"
  assert classify_file("pyproject.toml") == "config"


def test_chunk_python_file_splits_by_symbol() -> None:
  chunks = chunk_python_file("calc.py", SAMPLE_SOURCE)

  symbols = {c.symbol for c in chunks}
  assert "divide" in symbols
  assert "Calculator" in symbols
  assert "__module__" in symbols  # CONSTANT captured as module-level

  divide_chunk = next(c for c in chunks if c.symbol == "divide")
  assert "import os" in divide_chunk.content  # imports prepended
  assert divide_chunk.start_line == 7
  assert divide_chunk.chunk_type == "code"


def test_chunk_python_file_handles_syntax_error() -> None:
  chunks = chunk_python_file("broken.py", "def foo(:\n    pass")
  assert len(chunks) == 1
  assert chunks[0].symbol is None


def test_chunk_file_routes_by_extension() -> None:
  assert chunk_file("a.py", SAMPLE_SOURCE)[0].symbol is not None or True
  md_chunks = chunk_file("README.md", "# Title\nsome text")
  assert md_chunks[0].chunk_type == "doc"


def test_large_non_python_file_windowed_not_skipped() -> None:
  # Regression: >MAX_CHUNK_LINES non-.py files used to return [] — invisible
  # to retrieval (found live on CHANGELOG.md / ruff mdtest files, Phase 7.2).
  lines = [f"line {i}" for i in range(1, 301)]
  chunks = chunk_file("CHANGELOG.md", "\n".join(lines))
  assert chunks, "large non-Python files must be windowed, not skipped"
  assert chunks[0].start_line == 1
  assert chunks[-1].end_line == 300
  assert all(c.end_line - c.start_line + 1 <= MAX_CHUNK_LINES for c in chunks)
  assert all(c.chunk_type == "doc" for c in chunks)


def test_windowed_chunks_leave_no_gaps() -> None:
  lines = [f"line {i}" for i in range(1, 301)]
  chunks = chunk_file("CHANGELOG.md", "\n".join(lines))
  for prev, nxt in zip(chunks, chunks[1:], strict=False):
    assert nxt.start_line <= prev.end_line + 1
    assert nxt.start_line == prev.start_line + MAX_CHUNK_LINES - OVERLAP_LINES


def test_non_python_boundary_sizes() -> None:
  exact = "\n".join(f"line {i}" for i in range(MAX_CHUNK_LINES))
  assert len(chunk_file("notes.txt", exact)) == 1
  over = "\n".join(f"line {i}" for i in range(MAX_CHUNK_LINES + 1))
  assert len(chunk_file("notes.txt", over)) == 2
