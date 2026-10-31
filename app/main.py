import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import PROCESSING_DELAY_SECONDS
from .db import PROCESSING, SessionLocal, Transaction, engine, init_db, utcnow
from .worker import worker_loop


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop = asyncio.Event()
    task = asyncio.create_task(worker_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await task
        await engine.dispose()


app = FastAPI(title="Transaction Webhook Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip().rstrip("/") for origin in os.getenv("CORS_ORIGINS", "*").split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class WebhookPayload(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=128)
    source_account: str = Field(min_length=1, max_length=128)
    destination_account: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite returns naive datetimes
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@app.get("/")
async def health():
    return {"status": "HEALTHY", "current_time": _iso(utcnow())}


@app.post("/v1/webhooks/transactions", status_code=status.HTTP_202_ACCEPTED)
async def receive_webhook(payload: WebhookPayload):
    now = utcnow()
    txn = Transaction(
        transaction_id=payload.transaction_id,
        source_account=payload.source_account,
        destination_account=payload.destination_account,
        amount=payload.amount,
        currency=payload.currency,
        status=PROCESSING,
        created_at=now,
        process_after=now + timedelta(seconds=PROCESSING_DELAY_SECONDS),
    )
    try:
        async with SessionLocal() as session, session.begin():
            session.add(txn)
        return {"status": "accepted", "transaction_id": payload.transaction_id}
    except IntegrityError:
        # Acknowledge duplicate IDs without creating another transaction.
        log.info("duplicate webhook for %s ignored", payload.transaction_id)
        return {"status": "accepted", "transaction_id": payload.transaction_id, "duplicate": True}


@app.get("/v1/transactions/{transaction_id}")
async def get_transaction(transaction_id: str):
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Transaction).where(Transaction.transaction_id == transaction_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return [
        {
            "transaction_id": row.transaction_id,
            "source_account": row.source_account,
            "destination_account": row.destination_account,
            "amount": float(row.amount),
            "currency": row.currency,
            "status": row.status,
            "created_at": _iso(row.created_at),
            "processed_at": _iso(row.processed_at),
        }
    ]
