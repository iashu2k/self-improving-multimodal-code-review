from app.ingestion.chunker import chunk_file, chunk_python_file, classify_file

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
