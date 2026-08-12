from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
  logger.info(
    "application_starting",
    app_name=settings.app_name,
    environment=settings.app_env,
  )
  yield
  logger.info("application_stopping", app_name=settings.app_name)


app = FastAPI(
  title=settings.app_name,
  version="0.1.0",
  description=(
    "A GitHub App that performs grounded multimodal code review with "
    "LangGraph, OpenRouter, repository context, and evaluation-driven "
    "prompt and policy improvement."
  ),
  debug=settings.debug,
  lifespan=lifespan,
)

app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
  return {
    "service": settings.app_name,
    "docs": "/docs",
    "health": f"{settings.api_prefix}/health",
  }
