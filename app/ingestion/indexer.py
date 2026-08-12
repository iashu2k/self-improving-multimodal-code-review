import io
import tarfile

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.repo_index import CodeChunk, RepoSnapshot
from app.github.client import GitHubClient
from app.ingestion.chunker import chunk_file
from app.llm.openrouter_client import OpenRouterClient

logger = structlog.get_logger(__name__)


INDEXABLE_SUFFIXES = (
  ".py",
  ".md",
  ".toml",
  ".cfg",
  ".txt",
  # Frontend source (Phase 5): without these, a UI repo's index is empty
  # of application code and RAG has nothing real to retrieve.
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".css",
)
VENDORED_DIR_NAMES = frozenset(
  {"node_modules", "vendor", ".venv", "venv", "dist", ".git", "__pycache__"}
)
MAX_FILES = 200
EMBED_BATCH = 50


def is_vendored_path(path: str) -> bool:
  """Dependency/build directories: never index, never embed.

  Vendored deps are retrieval noise and a huge embedding workload — first
  seen live on review-sandbox-ui (Phase 5): ~200MB vendored node_modules,
  thousands of .md/.txt files that would have filled the MAX_FILES cap
  with junk while indexing zero application source.
  """
  return any(part in VENDORED_DIR_NAMES for part in path.split("/")[:-1])


async def get_or_create_snapshot(
  session: AsyncSession, *, owner: str, repo: str, sha: str
) -> RepoSnapshot:
  snapshot = await session.scalar(
    select(RepoSnapshot).where(
      RepoSnapshot.repo_owner == owner,
      RepoSnapshot.repo_name == repo,
      RepoSnapshot.sha == sha,
    )
  )
  if snapshot is None:
    snapshot = RepoSnapshot(repo_owner=owner, repo_name=repo, sha=sha, status="indexing")
    session.add(snapshot)
    await session.flush()
  return snapshot


async def index_snapshot(
  session: AsyncSession,
  *,
  snapshot: RepoSnapshot,
  github: GitHubClient,
  llm: OpenRouterClient,
) -> None:
  if snapshot.status == "indexed":
    return

  archive = await github.get_repo_archive(snapshot.repo_owner, snapshot.repo_name, snapshot.sha)

  file_count = 0
  vendored_skipped = 0
  chunks_to_add: list[CodeChunk] = []

  with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
    for member in tar.getmembers():
      if file_count >= MAX_FILES:
        break
      if not member.isfile() or not member.name.endswith(INDEXABLE_SUFFIXES):
        continue
      # Tarball paths are prefixed with owner-repo-sha/; strip first segment
      rel_path = "/".join(member.name.split("/")[1:])
      if not rel_path:
        continue
      if is_vendored_path(rel_path):
        vendored_skipped += 1
        continue

      extracted = tar.extractfile(member)
      if extracted is None:
        continue
      try:
        source = extracted.read().decode("utf-8", errors="replace")
      except Exception:
        continue

      file_count += 1
      for chunk in chunk_file(rel_path, source):
        chunks_to_add.append(
          CodeChunk(
            snapshot_id=snapshot.id,
            file_path=chunk.file_path,
            chunk_type=chunk.chunk_type,
            symbol=chunk.symbol,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            content=chunk.content,
          )
        )

  # Embed in batches
  if settings.openrouter_embedding_model:
    for i in range(0, len(chunks_to_add), EMBED_BATCH):
      batch = chunks_to_add[i : i + EMBED_BATCH]
      embeddings = await llm.embed(
        model=settings.openrouter_embedding_model,
        texts=[c.content[:4000] for c in batch],
      )
      for chunk, embedding in zip(batch, embeddings, strict=True):
        chunk.embedding = embedding

  session.add_all(chunks_to_add)
  snapshot.file_count = file_count
  snapshot.chunk_count = len(chunks_to_add)
  snapshot.status = "indexed"
  await session.flush()

  logger.info(
    "snapshot_indexed",
    sha=snapshot.sha[:8],
    files=file_count,
    chunks=len(chunks_to_add),
    vendored_skipped=vendored_skipped,
  )
