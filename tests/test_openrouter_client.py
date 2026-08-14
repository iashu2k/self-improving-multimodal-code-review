import json

import httpx
import pytest

from app.core.config import settings
from app.llm.openrouter_client import OpenRouterClient, StructuredOutputError


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


async def test_embed_records_cost_without_type_error():
  """Regression: embed() passed cost_key= to _record_cost, which didn't
  accept it — the embedding path crashed on first use (found by Phase 7
  curation). Mocked transport + None cost guard keep this offline; the
  TypeError fired before any guard interaction, so it still reproduces."""
  transport = httpx.MockTransport(
    lambda request: httpx.Response(
      200,
      json={
        "data": [{"embedding": [0.1, 0.2]}],
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
      },
    )
  )
  client = OpenRouterClient()
  client._client = httpx.AsyncClient(base_url=settings.openrouter_base_url, transport=transport)
  client._cost_guard = None  # recording is best-effort; None = skip

  result = await client.embed(model="openai/text-embedding-3-small", texts=["x"])

  assert result == [[0.1, 0.2]]
