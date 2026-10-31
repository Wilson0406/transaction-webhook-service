import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import DATABASE_URL

PROCESSING = "PROCESSING"
PROCESSED = "PROCESSED"


def _normalise_url(raw: str):
    """Accept postgres:// / postgresql:// URLs (as given by Supabase, Neon, Render)
    and convert them to the asyncpg driver. Returns (url, connect_args)."""
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        raw = "postgresql+asyncpg://" + raw[len("postgresql://"):]
    url = make_url(raw)
    connect_args = {}
    if url.drivername.startswith("postgresql+asyncpg"):
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        local = url.host in (None, "localhost", "127.0.0.1")
        if sslmode != "disable" and not local:
            # Managed database certificates may not be trusted by asyncpg's default verifier.
            connect_args["ssl"] = "require"
        # Transaction-pooling PgBouncer does not support asyncpg's prepared statements.
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0
        connect_args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid.uuid4().hex}__"
        url = url.set(query=query)
    return url, connect_args


_url, _connect_args = _normalise_url(DATABASE_URL)
engine = create_async_engine(_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_account: Mapped[str] = mapped_column(String(128))
    destination_account: Mapped[str] = mapped_column(String(128))
    amount: Mapped[float] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), index=True, default=PROCESSING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    process_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
