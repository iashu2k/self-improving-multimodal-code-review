import copy
import json
import ssl
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.redis import get_redis
from app.llm.cost_guard import CostGuard
from app.observability import llm_generation

logger = structlog.get_logger(__name__)


class Usage(BaseModel):
  prompt_tokens: int = 0
  completion_tokens: int = 0
  total_tokens: int | None = None


class StructuredResponse(BaseModel):
  content: dict[str, Any]
  usage: Usage
  model: str


class StructuredOutputError(RuntimeError):
  """Raised when the model returns content that is not valid structured JSON."""


def is_retryable_exception(exc: BaseException) -> bool:
  if isinstance(exc, httpx.HTTPStatusError):
    status_code = exc.response.status_code
    return status_code == 429 or status_code >= 500

  # Transport-layer failures (TLS resets, dropped connections, timeouts)
  # never produce an HTTP status — they are transient by nature and must
  # retry like 429/5xx. First seen live: SSLV3_ALERT_BAD_RECORD_MAC killing
  # an embeddings call mid-indexing (Phase 5).
  if isinstance(exc, httpx.TransportError | ssl.SSLError):
    return True

  # DailyCostCapExceeded is deliberately absent: retrying a cap violation
  # just delays the same answer.
  return isinstance(exc, json.JSONDecodeError | ValidationError | StructuredOutputError)


def _log_retry(retry_state) -> None:
  logger.warning(
    "openrouter_retry",
    attempt=retry_state.attempt_number,
    error=str(retry_state.outcome.exception())[:200],
  )


def raise_for_status_with_body(response: httpx.Response) -> None:
  """Like raise_for_status, but the exception carries the response body.



  OpenRouter's 4xx bodies name the exact problem (unsupported parameter,
  missing modality, payment) — discarding them makes every failure a
  guessing game. Retry semantics unchanged: still an HTTPStatusError, so
  is_retryable_exception's status-code logic applies verbatim.
  """
  if response.status_code < 400:
    return
  body = response.text[:500]
  raise httpx.HTTPStatusError(
    f"{response.status_code} {response.reason_phrase} — body: {body}",
    request=response.request,
    response=response,
  )


def strictify_schema(schema: dict[str, Any]) -> dict[str, Any]:
  """Adapt a pydantic JSON schema for OpenAI-style strict structured outputs.




  OpenAI's strict mode rejects a schema unless every object node sets
  additionalProperties: false and lists every property in `required`.
  Pydantic emits neither by default: fields with defaults (e.g.
  observations/uncertainties = []) are omitted from `required`, and
  additionalProperties is left unset. First seen live: gpt-4o-mini 400,
  "'additionalProperties' is required to be supplied and to be false".




  Also strips JSON Schema keywords some providers reject outright in
  structured-output schemas — Anthropic: "For 'array' type, property
  'maxItems' is not supported" (seen live routing claude-haiku via
  Azure/Bedrock). Local pydantic validation still enforces them on the
  parsed response; the wire schema only needs to describe shape.




  Lenient providers (Gemini, Qwen, Gemma) accept the same additions —
  both are standard JSON Schema — so the shim applies unconditionally:
  one request code path, no per-provider branching.




  Returns a new dict; the caller's schema is never mutated (the gate
  script reuses one schema across runs). Map-typed objects (no
  `properties`, additionalProperties already a schema) are left alone.
  """

  # Rejected by Anthropic and some Bedrock/Azure structured-output routes.
  _UNSUPPORTED = ("maxItems", "minItems", "minProperties", "maxProperties")

  def _walk(node: dict[str, Any]) -> None:
    for key in _UNSUPPORTED:
      node.pop(key, None)
    if node.get("type") == "object" and "properties" in node:
      node["additionalProperties"] = False
      node["required"] = list(node["properties"])
    for prop in node.get("properties", {}).values():
      if isinstance(prop, dict):
        _walk(prop)
    items = node.get("items")
    if isinstance(items, dict):
      _walk(items)
    for key in ("anyOf", "allOf", "oneOf"):
      for branch in node.get(key, []):
        if isinstance(branch, dict):
          _walk(branch)
    for definition in node.get("$defs", {}).values():
      if isinstance(definition, dict):
        _walk(definition)

  adapted = copy.deepcopy(schema)

  _walk(adapted)
  return adapted


class OpenRouterClient:
  def __init__(self) -> None:
    if not settings.openrouter_api_key:
      raise RuntimeError("OPENROUTER_API_KEY is not configured")

    self._client = httpx.AsyncClient(
      base_url=settings.openrouter_base_url,
      headers={
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_site_url,
        "X-Title": settings.openrouter_app_name,
        "Content-Type": "application/json",
      },
      timeout=httpx.Timeout(120.0),
    )
    self._cost_guard = CostGuard(get_redis(), settings.openrouter_daily_cost_cap_usd)

  async def aclose(self) -> None:
    await self._client.aclose()

  async def _record_cost(
    self,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_key: str = "chat",
  ) -> float | None:
    """Best-effort spend recording.



    The HTTP call has already succeeded when this runs — the money is
    spent either way, so a Redis failure here must not fail the caller's
    request. It MUST be loud, though: an unrecorded call means the daily
    cap is undercounting. (The pre-call check stays fail-closed.)



    cost_key tags the call type ("chat" | "embedding") in the
    openrouter_cost event, so eval runs can separate indexing spend from
    generation spend (Phase 7 cost logging).



    Returns the recorded cost in USD so callers can attach it to a
    Langfuse generation, or None when recording was skipped or failed.
    """
    if self._cost_guard is None:
      return None
    try:
      cost = await self._cost_guard.record(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
      )
      logger.info(
        "openrouter_cost",
        model=model,
        cost_key=cost_key,
        cost_usd=round(cost, 6),
        spent_today_usd=round(await self._cost_guard.spent_today(), 4),
      )
      return cost
    except Exception as exc:
      logger.warning("openrouter_cost_record_failed", error=str(exc)[:200])
      return None

  @retry(
    retry=retry_if_exception(is_retryable_exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    before_sleep=_log_retry,
    reraise=True,
  )
  async def chat_structured(
    self,
    *,
    model: str,
    schema_name: str,
    json_schema: dict[str, Any],
    messages: list[dict[str, Any]],
    temperature: float = 0.0,
    max_tokens: int = 1500,
    trace_name: str | None = None,
    prompt_version: str | None = None,
  ) -> StructuredResponse:
    if self._cost_guard is not None:
      await self._cost_guard.check()

    payload = {
      "model": model,
      "messages": messages,
      "temperature": temperature,
      "max_tokens": max_tokens,
      "provider": {
        "require_parameters": True,
      },
      "response_format": {
        "type": "json_schema",
        "json_schema": {
          "name": schema_name,
          "strict": True,
          "schema": strictify_schema(json_schema),
        },
      },
    }

    # One generation per attempt: the tenacity decorator re-enters this
    # method on retry, so retried calls show up as repeated generations
    # with failed attempts marked ERROR. That is the trace's retry count.
    async with llm_generation(
      trace_name or f"openrouter.{schema_name}",
      model=model,
      input=messages,
      prompt_version=prompt_version,
      metadata={
        "schema_name": schema_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
      },
    ) as generation:
      response = await self._client.post("/chat/completions", json=payload)
      raise_for_status_with_body(response)

      data = response.json()
      choices = data.get("choices", [])

      if not choices:
        raise StructuredOutputError(
          f"OpenRouter returned no choices. Response keys: {list(data.keys())}"
        )

      raw_content = choices[0].get("message", {}).get("content")

      if not isinstance(raw_content, str) or not raw_content.strip():
        message = choices[0].get("message", {})
        finish_reason = choices[0].get("finish_reason")
        reasoning = message.get("reasoning")
        raise StructuredOutputError(
          "OpenRouter returned empty or non-string message content. "
          f"model={model!r}, finish_reason={finish_reason!r}, "
          f"has_reasoning={bool(reasoning)}, usage={data.get('usage', {})!r}"
        )

      try:
        parsed_content = json.loads(raw_content)
      except json.JSONDecodeError as exc:
        preview = raw_content[:500].replace("\n", "\\n")
        raise StructuredOutputError(
          f"Invalid JSON from model={model}; content preview={preview!r}"
        ) from exc

      if not isinstance(parsed_content, dict):
        raise StructuredOutputError(
          f"Expected a JSON object from model={model}, got {type(parsed_content).__name__}."
        )

      try:
        usage = Usage.model_validate(data.get("usage", {}))
      except ValidationError as exc:
        raise StructuredOutputError(f"Invalid usage object from model={model}.") from exc

      result = StructuredResponse(
        content=parsed_content,
        usage=usage,
        model=data.get("model", model),
      )

      # Bill the provider-REPORTED model, not the requested one — an
      # OpenRouter reroute bills differently than the request implies.
      cost = await self._record_cost(
        model=result.model,
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
      )
      generation.update(
        output=result.content,
        model=result.model,
        usage_details={
          "input": result.usage.prompt_tokens,
          "output": result.usage.completion_tokens,
          "total": result.usage.total_tokens
          or result.usage.prompt_tokens + result.usage.completion_tokens,
        },
        cost_details={"total": cost} if cost is not None else None,
        metadata={"requested_model": model},
      )
      return result

  @retry(
    retry=retry_if_exception(is_retryable_exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    before_sleep=_log_retry,
    reraise=True,
  )
  async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
    if self._cost_guard is not None:
      await self._cost_guard.check()

    payload = {
      "model": model,
      "input": texts,
      "encoding_format": "float",
    }

    # Raw texts stay out of the trace input (they can be whole files);
    # the count is enough to debug indexing cost.
    async with llm_generation(
      "openrouter.embed",
      model=model,
      input={"text_count": len(texts)},
      metadata={"cost_key": "embedding"},
    ) as generation:
      response = await self._client.post("/embeddings", json=payload)
      raise_for_status_with_body(response)
      data = response.json()

      # Embeddings have no completions: prompt_tokens IS the whole usage
      # (total_tokens is the fallback for providers that only report it).
      # Passing total as completion_tokens is wrong under any pricing table
      # with a nonzero output rate — e.g. the unknown-model fallback.
      usage = data.get("usage", {})
      prompt_tokens = usage.get("prompt_tokens", 0) or usage.get("total_tokens", 0)
      cost = await self._record_cost(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
        cost_key="embedding",
      )
      generation.update(
        output={"embedding_count": len(data["data"])},
        usage_details={"input": prompt_tokens, "output": 0, "total": prompt_tokens},
        cost_details={"total": cost} if cost is not None else None,
      )

      return [item["embedding"] for item in data["data"]]
