import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.config import ReviewConfiguration
from app.db.session import get_db
from app.services.configurations import (
  ConfigurationConflictError,
  create_configuration_candidate,
)
from app.services.promotion import (
  approve_configuration,
  promote_configuration,
  reject_configuration,
  rollback_active_configuration,
)

router = APIRouter(prefix="/configurations", tags=["configurations"])


class ConfigurationCreateRequest(BaseModel):
  config_version: str = Field(min_length=1, max_length=64)
  parent_version: str | None = Field(default=None, max_length=64)
  change_reason: str = Field(min_length=1)
  generator_prompt_version: str = Field(min_length=1, max_length=64)
  critic_prompt_version: str = Field(min_length=1, max_length=64)
  router_rules: dict = Field(default_factory=dict)
  thresholds: dict = Field(default_factory=dict)
  model_versions: dict = Field(default_factory=dict)
  retrieval_config: dict = Field(default_factory=dict)
  repair_policy: dict = Field(default_factory=dict)
  created_by: str = Field(default="manual", max_length=128)


class ApprovalRequest(BaseModel):
  approved_by: str = Field(min_length=1, max_length=128)


class RejectionRequest(BaseModel):
  reason: str = Field(min_length=1)


class RollbackRequest(BaseModel):
  reason: str = Field(min_length=1)


class PromotionRequest(BaseModel):
  system: str = Field(default="final_agent", min_length=1, max_length=64)


class ConfigurationResponse(BaseModel):
  id: str
  config_version: str
  parent_version: str | None
  change_reason: str
  status: str
  approval_status: str
  router_rules: dict
  generator_prompt_version: str
  critic_prompt_version: str
  thresholds: dict
  model_versions: dict
  retrieval_config: dict
  repair_policy: dict
  evaluation_summary: dict
  created_by: str
  created_at: str
  approved_by: str | None
  approved_at: str | None
  promoted_at: str | None
  rejected_at: str | None
  rejection_reason: str | None
  rolled_back_at: str | None
  rollback_reason: str | None


class ConfigurationPage(BaseModel):
  items: list[ConfigurationResponse]
  limit: int
  offset: int
  total: int


def serialize_configuration(config: ReviewConfiguration) -> ConfigurationResponse:
  approval_status = "approved" if config.approved_by else "pending_approval"

  return ConfigurationResponse(
    id=str(config.id),
    config_version=config.config_version,
    parent_version=config.parent_version,
    change_reason=config.change_reason,
    status=config.status,
    approval_status=approval_status,
    router_rules=config.router_rules,
    generator_prompt_version=config.generator_prompt_version,
    critic_prompt_version=config.critic_prompt_version,
    thresholds=config.thresholds,
    model_versions=config.model_versions,
    retrieval_config=config.retrieval_config,
    repair_policy=config.repair_policy,
    evaluation_summary=config.evaluation_summary,
    created_by=config.created_by,
    created_at=config.created_at.isoformat(),
    approved_by=config.approved_by,
    approved_at=config.approved_at.isoformat() if config.approved_at else None,
    promoted_at=config.promoted_at.isoformat() if config.promoted_at else None,
    rejected_at=config.rejected_at.isoformat() if config.rejected_at else None,
    rejection_reason=config.rejection_reason,
    rolled_back_at=config.rolled_back_at.isoformat() if config.rolled_back_at else None,
    rollback_reason=config.rollback_reason,
  )


def configuration_not_found(exc: ValueError) -> HTTPException:
  if "does not exist" in str(exc):
    return HTTPException(status_code=404, detail=str(exc))
  return HTTPException(status_code=400, detail=str(exc))


@router.post("", response_model=ConfigurationResponse, status_code=201)
async def create_configuration(
  request: ConfigurationCreateRequest,
  db: Annotated[AsyncSession, Depends(get_db)],
) -> ConfigurationResponse:
  try:
    config = await create_configuration_candidate(
      db,
      config_version=request.config_version,
      parent_version=request.parent_version,
      change_reason=request.change_reason,
      generator_prompt_version=request.generator_prompt_version,
      critic_prompt_version=request.critic_prompt_version,
      router_rules=request.router_rules,
      thresholds=request.thresholds,
      model_versions=request.model_versions,
      retrieval_config=request.retrieval_config,
      repair_policy=request.repair_policy,
      created_by=request.created_by,
    )
    await db.commit()
  except ConfigurationConflictError as exc:
    await db.rollback()
    raise HTTPException(status_code=409, detail=str(exc)) from exc
  except ValueError as exc:
    await db.rollback()
    raise HTTPException(status_code=400, detail=str(exc)) from exc

  return serialize_configuration(config)


@router.get("", response_model=ConfigurationPage)
async def list_configurations(
  db: Annotated[AsyncSession, Depends(get_db)],
  limit: int = Query(default=50, ge=1, le=200),
  offset: int = Query(default=0, ge=0),
) -> ConfigurationPage:
  total = await db.scalar(select(func.count()).select_from(ReviewConfiguration))
  rows = (
    await db.scalars(
      select(ReviewConfiguration)
      .order_by(ReviewConfiguration.created_at.desc())
      .limit(limit)
      .offset(offset)
    )
  ).all()

  return ConfigurationPage(
    items=[serialize_configuration(config) for config in rows],
    limit=limit,
    offset=offset,
    total=total or 0,
  )


@router.post("/{configuration_id}/approve", response_model=ConfigurationResponse)
async def approve_configuration_route(
  configuration_id: uuid.UUID,
  request: ApprovalRequest,
  db: Annotated[AsyncSession, Depends(get_db)],
) -> ConfigurationResponse:
  try:
    config = await approve_configuration(
      db,
      configuration_id=configuration_id,
      approved_by=request.approved_by,
    )
    await db.commit()
  except ValueError as exc:
    await db.rollback()
    raise configuration_not_found(exc) from exc

  return serialize_configuration(config)


@router.post("/{configuration_id}/promote")
async def promote_configuration_route(
  configuration_id: uuid.UUID,
  request: PromotionRequest,
  db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
  try:
    decision = await promote_configuration(
      db,
      configuration_id=configuration_id,
      system=request.system,
    )
    await db.commit()
  except ValueError as exc:
    await db.rollback()
    raise configuration_not_found(exc) from exc

  return {
    "eligible": decision.eligible,
    "failed_conditions": decision.failed_conditions,
  }


@router.post("/{configuration_id}/reject", response_model=ConfigurationResponse)
async def reject_configuration_route(
  configuration_id: uuid.UUID,
  request: RejectionRequest,
  db: Annotated[AsyncSession, Depends(get_db)],
) -> ConfigurationResponse:
  try:
    config = await reject_configuration(
      db,
      configuration_id=configuration_id,
      reason=request.reason,
    )
    await db.commit()
  except ValueError as exc:
    await db.rollback()
    raise configuration_not_found(exc) from exc

  return serialize_configuration(config)


@router.post("/rollback", response_model=ConfigurationResponse)
async def rollback_configuration_route(
  request: RollbackRequest,
  db: Annotated[AsyncSession, Depends(get_db)],
) -> ConfigurationResponse:
  try:
    config = await rollback_active_configuration(
      db,
      reason=request.reason,
    )
    await db.commit()
  except ValueError as exc:
    await db.rollback()
    raise HTTPException(status_code=400, detail=str(exc)) from exc

  return serialize_configuration(config)
