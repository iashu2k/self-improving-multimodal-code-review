import httpx
from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class StructuredResponse(BaseModel):
    content: dict
    usage: Usage
    model: str


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


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
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def chat_structured(
        self,
        *,
        model: str,
        schema_name: str,
        json_schema: dict,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> StructuredResponse:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
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

        import json

        return StructuredResponse(
            content=json.loads(data["choices"][0]["message"]["content"]),
            usage=Usage(**data.get("usage", {})),
            model=data.get("model", model),
        )
