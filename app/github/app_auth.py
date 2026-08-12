import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt

from app.core.config import settings

GITHUB_API_BASE = "https://api.github.com"

TOKEN_EXPIRY_BUFFER_SECONDS = 300


@dataclass
class CachedToken:
  token: str
  expires_at: float


_installation_tokens: dict[int, CachedToken] = {}


def load_private_key() -> str:
  if not settings.github_private_key_path:
    raise RuntimeError("GITHUB_PRIVATE_KEY_PATH is not configured")

  key_path = Path(settings.github_private_key_path)
  if not key_path.is_file():
    raise RuntimeError(f"GitHub private key not found at {key_path}")

  return key_path.read_text()


def create_app_jwt() -> str:
  if settings.github_app_id is None:
    raise RuntimeError("GITHUB_APP_ID is not configured")

  now = int(time.time())
  payload = {
    "iat": now - 60,  # issued 60s in the past to tolerate clock skew
    "exp": now + 600,  # GitHub maximum: 10 minutes
    "iss": str(settings.github_app_id),
  }
  return jwt.encode(payload, load_private_key(), algorithm="RS256")


async def get_installation_token(
  installation_id: int,
  *,
  http_client: httpx.AsyncClient | None = None,
) -> str:
  cached = _installation_tokens.get(installation_id)
  if cached and cached.expires_at - TOKEN_EXPIRY_BUFFER_SECONDS > time.time():
    return cached.token

  app_jwt = create_app_jwt()
  owns_client = http_client is None
  client = http_client or httpx.AsyncClient(base_url=GITHUB_API_BASE, timeout=httpx.Timeout(30.0))

  try:
    response = await client.post(
      f"/app/installations/{installation_id}/access_tokens",
      headers={
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    )
    response.raise_for_status()
    data = response.json()
  finally:
    if owns_client:
      await client.aclose()

  expires_at = (
    datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).astimezone(UTC).timestamp()
  )

  _installation_tokens[installation_id] = CachedToken(token=data["token"], expires_at=expires_at)
  return data["token"]


def clear_token_cache() -> None:
  """Used by tests to isolate cached-token behavior."""
  _installation_tokens.clear()
