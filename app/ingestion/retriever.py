from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.github.diff_parser import ChangedFile
from app.llm.openrouter_client import OpenRouterClient

TOP_K = 5
CANDIDATES_PER_LEG = 20
MAX_CONTEXT_QUERY_FILES = 5
MAX_CONTEXTS_FOR_PROMPT = 8


@dataclass
class RetrievedContext:
    file_path: str
    symbol: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str


def build_context_query(changed_file: ChangedFile) -> str:
    added_lines = [
        line.content for hunk in changed_file.hunks for line in hunk.lines if line.kind == "add"
    ]
    symbols = " ".join(added_lines)[:500]
    return f"{changed_file.path} {symbols}"


async def hybrid_retrieve(
    session: AsyncSession,
    *,
    snapshot_id: int,
    query_text: str,
    llm: OpenRouterClient,
    embedding_model: str | None,
) -> list[RetrievedContext]:
    """Reciprocal-rank-fusion over pgvector cosine + Postgres FTS."""
    embedding: list[float] | None = None
    if embedding_model:
        [embedding] = await llm.embed(model=embedding_model, texts=[query_text[:4000]])

    sql = text(
        """
        WITH vector_leg AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> :embedding) AS rank
            FROM code_chunks
            WHERE snapshot_id = :sid AND embedding IS NOT NULL
            LIMIT :k
        ),
        fts_leg AS (
            SELECT id, ROW_NUMBER() OVER (
              ORDER BY ts_rank(to_tsvector('english', content), plainto_tsquery('english', :q)) DESC
            ) AS rank
            FROM code_chunks
            WHERE snapshot_id = :sid
              AND to_tsvector('english', content) @@ plainto_tsquery('english', :q)
            LIMIT :k
        ),
        fused AS (
            SELECT COALESCE(v.id, f.id) AS id,
                   COALESCE(1.0 / (60 + v.rank), 0) + COALESCE(1.0 / (60 + f.rank), 0) AS rrf
            FROM vector_leg v
            FULL OUTER JOIN fts_leg f ON v.id = f.id
        )
        SELECT c.file_path, c.symbol, c.chunk_type, c.start_line, c.end_line, c.content
        FROM fused
        JOIN code_chunks c ON c.id = fused.id
        ORDER BY fused.rrf DESC
        LIMIT :top_k
        """
    )

    rows = (
        await session.execute(
            sql,
            {
                "sid": snapshot_id,
                "embedding": str(embedding) if embedding else None,
                "q": query_text,
                "k": CANDIDATES_PER_LEG,
                "top_k": TOP_K,
            },
        )
    ).all()

    return [
        RetrievedContext(
            file_path=r.file_path,
            symbol=r.symbol,
            chunk_type=r.chunk_type,
            start_line=r.start_line,
            end_line=r.end_line,
            content=r.content,
        )
        for r in rows
    ]
