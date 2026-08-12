from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.jobs import run_pr_review


def _redis_settings() -> RedisSettings:
  if not settings.redis_url:
    raise RuntimeError("REDIS_URL is not configured")
  return RedisSettings.from_dsn(str(settings.redis_url))


class WorkerSettings:
  functions = [run_pr_review]
  redis_settings = _redis_settings()
  max_jobs = 4
  job_timeout = 300
