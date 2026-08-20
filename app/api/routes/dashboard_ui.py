"""Phase 9 dashboard UI: server-rendered read-only pages.

Thin HTML shell over the dashboard JSON handlers in
app/api/routes/dashboard.py. Those handlers are plain async functions,
so the pages call them directly with explicit arguments and render the
same response models. No query logic is duplicated, and every mutation
stays on the versioned JSON API.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import dashboard as dashboard_api
from app.db.session import get_db

router = APIRouter(
  prefix="/dashboard",
  tags=["dashboard-ui"],
  include_in_schema=False,
)
templates = Jinja2Templates(directory="app/templates")

PAGE_SIZE = 50


@router.get("/")
async def home() -> RedirectResponse:
  return RedirectResponse(url="/dashboard/configurations")


@router.get("/configurations")
async def configurations_page(
  request: Request,
  db: Annotated[AsyncSession, Depends(get_db)],
  status: str | None = None,
) -> Any:
  data = await dashboard_api.list_configurations(db=db, status=status or None, limit=100, offset=0)
  return templates.TemplateResponse(
    request=request,
    name="dashboard/configurations.html",
    context={"data": data, "status": status or ""},
  )


@router.get("/runs")
async def runs_page(
  request: Request,
  db: Annotated[AsyncSession, Depends(get_db)],
  status: str | None = None,
  page: int = 1,
) -> Any:
  page = max(page, 1)
  data = await dashboard_api.list_runs(
    db=db, status=status or None, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
  )
  return templates.TemplateResponse(
    request=request,
    name="dashboard/runs.html",
    context={"data": data, "status": status or "", "page": page},
  )


@router.get("/runs/{run_id}")
async def run_detail_page(
  request: Request,
  run_id: int,
  db: Annotated[AsyncSession, Depends(get_db)],
) -> Any:
  data = await dashboard_api.run_detail(run_id=run_id, db=db)
  return templates.TemplateResponse(
    request=request,
    name="dashboard/run_detail.html",
    context={"data": data},
  )


@router.get("/evaluation")
async def evaluation_page(
  request: Request,
  db: Annotated[AsyncSession, Depends(get_db)],
  config_version: str | None = None,
) -> Any:
  data = await dashboard_api.evaluation_overview(
    db=db, config_version=config_version or None, limit=20
  )
  return templates.TemplateResponse(
    request=request,
    name="dashboard/evaluation.html",
    context={"data": data, "config_version": config_version or ""},
  )


@router.get("/feedback")
async def feedback_page(
  request: Request,
  db: Annotated[AsyncSession, Depends(get_db)],
  days: int = 30,
) -> Any:
  data = await dashboard_api.feedback_overview(db=db, days=days)
  return templates.TemplateResponse(
    request=request,
    name="dashboard/feedback.html",
    context={"data": data, "days": days},
  )
