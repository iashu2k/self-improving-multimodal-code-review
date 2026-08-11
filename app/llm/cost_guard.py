"""Daily spend cap for OpenRouter calls.

Prices are a LOCAL table (USD per 1M tokens) — refresh from
openrouter.ai/models when adding models. Estimates err conservative:
unknown model => priced at the table maximum, never silently free.

Fail-closed: exceeding the cap raises before the HTTP call. The
exception is deliberately NOT matched by is_retryable_exception —
retrying a cap violation just delays the same answer.
"""

from __future__ import annotations

import datetime

MODEL_PRICING: dict[str, tuple[float, float]] = {
    # model: (input $/1M tokens, output $/1M tokens)
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "openai/text-embedding-3-small": (0.02, 0.0),
    "google/gemini-2.5-flash-lite": (0.10, 0.40),
}
_FALLBACK_PRICING = (5.0, 15.0)  # unknown model: assume expensive


class DailyCostCapExceeded(RuntimeError):
    """Raised before an API call when today's estimated spend >= cap."""


class CostGuard:
    def __init__(self, redis, cap_usd: float) -> None:
        self._redis = redis
        self._cap = cap_usd

    @staticmethod
    def _day_key() -> str:
        return f"cost:daily:{datetime.datetime.now(datetime.UTC):%Y-%m-%d}"

    async def spent_today(self) -> float:
        return float(await self._redis.get(self._day_key()) or 0.0)

    async def check(self) -> None:
        spent = await self.spent_today()
        if spent >= self._cap:
            raise DailyCostCapExceeded(
                f"Daily OpenRouter spend cap hit: ${spent:.4f} >= ${self._cap:.2f}"
            )

    async def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        price_in, price_out = MODEL_PRICING.get(model, _FALLBACK_PRICING)
        cost = (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000
        key = self._day_key()
        await self._redis.incrbyfloat(key, cost)
        # 2 days: survives debugging, self-cleans
        await self._redis.expire(key, 60 * 60 * 48)
        return cost
