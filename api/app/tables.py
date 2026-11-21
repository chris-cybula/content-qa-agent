# Database tables
# runs, pages, findings, fix_proposals and claude_calls (SQLAlchemy models; Alembic migrates them).

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    sitemap_url: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), default="running")

    error: Mapped[str | None] = mapped_column(Text, default=None)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    page_count: Mapped[int] = mapped_column(Integer, default=0)

    failed_count: Mapped[int] = mapped_column(Integer, default=0)

    pages: Mapped[list["Page"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Page.position",
    )


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)

    position: Mapped[int] = mapped_column(Integer)

    url: Mapped[str] = mapped_column(Text)

    final_url: Mapped[str | None] = mapped_column(Text, default=None)

    status_code: Mapped[int | None] = mapped_column(Integer, default=None)

    error: Mapped[str | None] = mapped_column(Text, default=None)

    title: Mapped[str | None] = mapped_column(Text, default=None)

    meta_description: Mapped[str | None] = mapped_column(Text, default=None)

    lang: Mapped[str | None] = mapped_column(String(35), default=None)

    word_count: Mapped[int] = mapped_column(Integer, default=0)

    text: Mapped[str] = mapped_column(Text, default="")

    headings: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)

    links: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)

    images: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[Run] = relationship(back_populates="pages")

    findings: Mapped[list["Finding"]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="Finding.id",
    )


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)

    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)

    rule: Mapped[str] = mapped_column(String(50))

    severity: Mapped[str] = mapped_column(String(10))

    element: Mapped[str] = mapped_column(Text)

    evidence: Mapped[str] = mapped_column(Text)

    suggested_fix: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    page: Mapped[Page] = relationship(back_populates="findings")


class FixProposal(Base):
    __tablename__ = "fix_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)

    finding_id: Mapped[int] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )

    new_text: Mapped[str] = mapped_column(Text)

    reason: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ClaudeCall(Base):
    __tablename__ = "claude_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), index=True, default=None
    )

    purpose: Mapped[str] = mapped_column(String(50))

    model: Mapped[str] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(20), default="reserved")

    max_tokens: Mapped[int] = mapped_column(Integer)

    reserved_microdollars: Mapped[int] = mapped_column(BigInteger)

    cost_microdollars: Mapped[int] = mapped_column(BigInteger)

    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        Computed("round(cost_microdollars / 1000000.0, 4)", persisted=True),
    )

    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)

    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)

    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, default=None)

    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, default=None)

    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    request_id: Mapped[str | None] = mapped_column(String(100), default=None)

    error: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
