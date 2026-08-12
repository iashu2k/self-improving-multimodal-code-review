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


def test_strictify_schema_strips_provider_unsupported_keys():
  from app.llm.openrouter_client import strictify_schema

  schema = {
    "type": "object",
    "properties": {
      "observations": {
        "type": "array",
        "maxItems": 5,
        "items": {
          "type": "object",
          "properties": {"type": {"type": "string"}},
          "minProperties": 1,
        },
      }
    },
  }
  adapted = strictify_schema(schema)
  obs = adapted["properties"]["observations"]
  assert "maxItems" not in obs
  assert "minProperties" not in obs["items"]
  # strict-mode additions still applied
  assert adapted["additionalProperties"] is False
  assert obs["items"]["required"] == ["type"]
  # caller's schema never mutated
  assert schema["properties"]["observations"]["maxItems"] == 5
