import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings


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

    return isinstance(exc, json.JSONDecodeError | ValidationError | StructuredOutputError)


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

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception(is_retryable_exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
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
    ) -> StructuredResponse:
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
                    "schema": json_schema,
                },
            },
        }

        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()

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

        return StructuredResponse(
            content=parsed_content,
            usage=usage,
            model=data.get("model", model),
        )
