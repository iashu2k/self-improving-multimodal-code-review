from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RepoSnapshot(Base):
    __tablename__ = "repo_snapshots"
    __table_args__ = (UniqueConstraint("repo_owner", "repo_name", "sha", name="uq_repo_snapshot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_owner: Mapped[str] = mapped_column(String(255))
    repo_name: Mapped[str] = mapped_column(String(255))
    sha: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="indexing")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    chunks: Mapped[list["CodeChunk"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("repo_snapshots.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(500), index=True)
    chunk_type: Mapped[str] = mapped_column(String(20))  # code | test | doc | config
    symbol: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    start_line: Mapped[int]
    end_line: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    snapshot: Mapped[RepoSnapshot] = relationship(back_populates="chunks")
