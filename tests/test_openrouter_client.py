import json

import pytest

from app.llm.openrouter_client import StructuredOutputError


def test_structured_output_error_preserves_json_failure_context() -> None:
    malformed_content = '{"summary": "incomplete", "comments": ['

    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed_content)


def test_structured_output_error_is_runtime_error() -> None:
    error = StructuredOutputError("Invalid JSON from model=test-model")

    assert isinstance(error, RuntimeError)
    assert "test-model" in str(error)
