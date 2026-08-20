from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select

from app.agents.graph import run_review_graph
from app.core.config import settings
from app.db.models.review import (
  CommentStatus,
  ReviewRun,
  ReviewRunEvent,
  RunStatus,
  StoredReviewComment,
)
from app.db.session import get_session_maker
from app.github.app_auth import get_installation_token
from app.github.client import GitHubClient
from app.github.diff_parser import ChangedFile, parse_unified_diff
from app.ingestion.indexer import get_or_create_snapshot, index_snapshot
from app.llm.openrouter_client import OpenRouterClient
from app.observability import flush, node_span, redacted_pr_metadata, review_run_trace
from app.vision.analyzer import analyze_pr_visual
from app.vision.review_bridge import build_visual_review_comments

logger = structlog.get_logger(__name__)


IGNORED_EXACT_NAMES = {
  "package-lock.json",
  "yarn.lock",
  "pnpm-lock.yaml",
  "poetry.lock",
  "uv.lock",
  "Pipfile.lock",
}


IGNORED_SUFFIXES = (
  ".min.js",
  ".min.css",
  ".map",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".svg",
  ".ico",
  ".pdf",
  ".zip",
  ".gz",
)


def is_reviewable(changed_file: ChangedFile) -> bool:
  if changed_file.status == "deleted":
    return False
  name = changed_file.path.rsplit("/", 1)[-1]
  if name in IGNORED_EXACT_NAMES:
    return False
  return not changed_file.path.endswith(IGNORED_SUFFIXES)


async def run_pr_review(
  ctx: dict,
  *,
  installation_id: int,
  repository_owner: str,
  repository_name: str,
  pr_number: int,
  pr_title: str,
  pr_body: str,
  head_sha: str,
  github_delivery_id: str | None = None,
) -> dict:
  log = logger.bind(
    repo=f"{repository_owner}/{repository_name}",
    pr_number=pr_number,
    head_sha=head_sha[:8],
  )
  log.info("review_job_started")

  session_maker = get_session_maker()

  async with session_maker() as session:
    # One run row per identity. PUBLISHED is terminal (skip); any other
    # status is reused and reset so retries continue the same run.
    run = await session.scalar(
      select(ReviewRun).where(
        ReviewRun.repo_owner == repository_owner,
        ReviewRun.repo_name == repository_name,
        ReviewRun.pr_number == pr_number,
        ReviewRun.head_sha == head_sha,
        ReviewRun.config_version == settings.config_version,
      )
    )

    if run and run.status == RunStatus.PUBLISHED:
      log.info("review_skipped_duplicate", run_id=run.id)
      return {"status": "skipped_duplicate", "run_id": run.id}

    if run:
      log.info("review_run_resumed", run_id=run.id, previous_status=run.status)
      run.status = RunStatus.RUNNING
      run.abstain_reason = None
      run.error = None
      run.completed_at = None
    else:
      run = ReviewRun(
        repo_owner=repository_owner,
        repo_name=repository_name,
        pr_number=pr_number,
        head_sha=head_sha,
        config_version=settings.config_version,
        status=RunStatus.RUNNING,
      )
      session.add(run)

    await session.commit()
    await session.refresh(run)

    # Phase 9: one Langfuse trace per review run. The trace ID is seeded
    # by run.id, so ARQ retries of the same run merge into a single trace
    # and the dashboard can deep-link to it without storing anything.
    # Fail-open: with Langfuse disabled or down, every helper here no-ops.
    try:
      async with review_run_trace(
        review_run_id=run.id,
        github_delivery_id=github_delivery_id,
        pr_metadata=redacted_pr_metadata(
          repo=f"{repository_owner}/{repository_name}",
          pr_number=pr_number,
          head_sha=head_sha,
        ),
        config_version=settings.config_version,
      ) as trace:
        try:
          token = await get_installation_token(installation_id)
          github = GitHubClient(token)
          llm = OpenRouterClient()

          try:
            diff_text = await github.get_pr_diff(repository_owner, repository_name, pr_number)
            files = parse_unified_diff(diff_text)
            reviewable = [f for f in files if is_reviewable(f)]

            log.info(
              "diff_fetched",
              total_files=len(files),
              reviewable_files=len(reviewable),
            )
            trace.update(
              metadata={
                "total_files": len(files),
                "reviewable_files": len(reviewable),
              }
            )

            if not reviewable:
              run.status = RunStatus.ABSTAINED
              run.abstain_reason = "no_reviewable_files"
              run.completed_at = datetime.now(UTC)
              await session.commit()
              trace.update(output={"status": "abstained", "reason": "no_reviewable_files"})
              return {"status": "abstained", "reason": "no_reviewable_files"}

            if not settings.openrouter_review_model:
              raise RuntimeError("OPENROUTER_REVIEW_MODEL is not configured")

            # --- index repo at head SHA (unchanged) ---
            snapshot = await get_or_create_snapshot(
              session,
              owner=repository_owner,
              repo=repository_name,
              sha=head_sha,
            )
            # Groups the embedding generations from index_snapshot under
            # one span instead of directly under the trace root.
            async with node_span(
              "repo_indexer",
              input={"snapshot_id": snapshot.id, "head_sha": head_sha[:8]},
            ):
              await index_snapshot(session, snapshot=snapshot, github=github, llm=llm)
              await session.commit()

            # --- Phase 5: visual analyzer (sandbox + capture + vision) ---
            # Adjust snapshot_root if your snapshots live elsewhere.
            repo_root = Path(__file__).resolve().parent.parent.parent  # adjust if needed

            # Runs outside the LangGraph callback path, so it needs an
            # explicit span to appear in the trace.
            async with node_span("vision_analyzer", input={"routes": ["/checkout"]}) as vision_span:
              visual_analysis = await analyze_pr_visual(
                repo_root=repo_root,
                pr_title=pr_title,
                diff_text=diff_text,
                routes=["/checkout"],
              )
              vision_span.update(
                output={
                  "sandbox_ok": visual_analysis.sandbox.ok,
                  "grounded_count": len(visual_analysis.grounded_observations),
                }
              )

            visual_comments = []
            if visual_analysis.sandbox.ok and visual_analysis.grounded_observations:
              visual_comments = build_visual_review_comments(visual_analysis.grounded_observations)
              log.info(
                "visual_analysis_completed",
                viewport_results=list(visual_analysis.per_viewport.keys()),
                grounded_count=len(visual_analysis.grounded_observations),
              )
            elif not visual_analysis.sandbox.ok:
              log.warning(
                "visual_sandbox_failed",
                stage_failed=visual_analysis.sandbox.stage_failed,
                error=visual_analysis.sandbox.error,
              )

            # --- Phase 4: agent graph -------------------------------
            output = await run_review_graph(
              session=session,
              llm=llm,
              snapshot_id=snapshot.id,
              run_id=run.id,
              pr_number=pr_number,
              commit_sha=head_sha,
              pr_title=pr_title,
              pr_body=pr_body,
              diff=diff_text,
              changed_files=reviewable,
              config_version=settings.config_version,
              router_model=settings.openrouter_router_model or settings.openrouter_review_model,
              review_model=settings.openrouter_review_model,
              critic_model=settings.openrouter_critic_model or settings.openrouter_review_model,
              embedding_model=settings.openrouter_embedding_model,
            )

            # Merge visual comments into the final review payload.
            if visual_comments:
              from app.github.formatting import format_comment_body

              for vc in visual_comments:
                output.accepted.append(vc)
                output.review_comments.append(
                  {
                    "path": vc.file_path,
                    "line": vc.line,
                    "side": vc.side,
                    "body": format_comment_body(vc, run_id=run.id),
                  }
                )

            for event in output.events:
              session.add(
                ReviewRunEvent(
                  run_id=run.id,
                  node=event["node"],
                  detail=event["detail"],
                )
              )

            for s in output.suppressed:
              session.add(
                StoredReviewComment(
                  run_id=run.id,
                  file_path=s.comment.file_path,
                  line=s.comment.line,
                  severity=s.comment.severity.value,
                  category=s.comment.category.value,
                  title=s.comment.title,
                  body=s.comment.body,
                  suggested_fix=s.comment.suggested_fix,
                  confidence=s.comment.confidence,
                  status=CommentStatus.SUPPRESSED,
                  suppression_reason=s.reason,
                )
              )

            if not output.should_publish:
              run.status = RunStatus.ABSTAINED
              run.abstain_reason = output.abstain_reason
              run.completed_at = datetime.now(UTC)
              await session.commit()
              log.info("review_abstained", reason=run.abstain_reason)
              trace.update(
                output={
                  "status": "abstained",
                  "reason": run.abstain_reason,
                  "comments_suppressed": len(output.suppressed),
                  "retry_count": output.retry_count,
                }
              )
              return {"status": "abstained", "reason": run.abstain_reason}

            current_head_sha = await github.get_pr_head_sha(
              repository_owner, repository_name, pr_number
            )

            log.info(
              "review_submitting",
              commit_id=current_head_sha,
              comments=[{"path": c["path"], "line": c["line"]} for c in output.review_comments],
            )

            review_response = await github.create_review(
              repository_owner,
              repository_name,
              pr_number,
              commit_id=current_head_sha,
              body=output.review_body,
              comments=output.review_comments,
            )

            for c in output.accepted:
              session.add(
                StoredReviewComment(
                  run_id=run.id,
                  file_path=c.file_path,
                  line=c.line,
                  severity=c.severity.value,
                  category=c.category.value,
                  title=c.title,
                  body=c.body,
                  suggested_fix=c.suggested_fix,
                  confidence=c.confidence,
                  status=CommentStatus.POSTED,
                )
              )

            run.status = RunStatus.PUBLISHED
            run.github_review_id = review_response.get("id")
            run.completed_at = datetime.now(UTC)
            await session.commit()

            log.info(
              "review_published",
              review_id=run.github_review_id,
              comment_count=len(output.review_comments),
              retry_count=output.retry_count,
            )
            trace.update(
              output={
                "status": "published",
                "github_review_id": run.github_review_id,
                "comments_posted": len(output.review_comments),
                "comments_suppressed": len(output.suppressed),
                "retry_count": output.retry_count,
              }
            )
            return {
              "status": "published",
              "comments": len(output.review_comments),
            }

          finally:
            await github.aclose()
            await llm.aclose()

        except Exception as exc:
          run.status = RunStatus.FAILED
          run.error = f"{type(exc).__name__}: {exc}"[:2000]
          run.completed_at = datetime.now(UTC)
          await session.commit()
          raise
    finally:
      # ARQ jobs are discrete units: flush buffered spans on every exit
      # path (published, abstained, failed) or they never ship.
      flush()
