import asyncio
import logging
from datetime import timedelta

from sqlalchemy import or_, select, update

from .config import WORKER_BATCH_SIZE, WORKER_POLL_INTERVAL_SECONDS, LEASE_SECONDS, MAX_RETRY_BACKOFF_SECONDS
from .db import PROCESSED, PROCESSING, SessionLocal, Transaction, utcnow

log = logging.getLogger("worker")


async def _claim_batch() -> list[str]:
    now = utcnow()
    async with SessionLocal() as session, session.begin():
        stmt = (
            select(Transaction)
            .where(
                Transaction.status == PROCESSING,
                Transaction.process_after <= now,
                or_(Transaction.locked_until.is_(None), Transaction.locked_until < now),
            )
            .order_by(Transaction.process_after)
            .limit(WORKER_BATCH_SIZE)
            .with_for_update(skip_locked=True)  # ignored on SQLite, used on Postgres
        )
        rows = (await session.execute(stmt)).scalars().all()
        lease = now + timedelta(seconds=LEASE_SECONDS)
        for row in rows:
            row.locked_until = lease
            row.attempts += 1
        return [r.transaction_id for r in rows]


async def _call_external_api(transaction_id: str) -> None:
    """Placeholder for the processor call; its delay is scheduled with process_after."""
    await asyncio.sleep(0)


async def _process_one(transaction_id: str) -> None:
    try:
        await _call_external_api(transaction_id)
        async with SessionLocal() as session, session.begin():
            await session.execute(
                update(Transaction)
                .where(
                    Transaction.transaction_id == transaction_id,
                    Transaction.status == PROCESSING,
                )
                .values(status=PROCESSED, processed_at=utcnow(), locked_until=None)
            )
        log.info("processed %s", transaction_id)
    except Exception:
        log.exception("failed processing %s; will retry", transaction_id)
        # Keep the row retryable and delay the next attempt using the lease.
        async with SessionLocal() as session, session.begin():
            row = await session.get(Transaction, transaction_id)
            if row:
                backoff = min(2 ** (row.attempts or 1), MAX_RETRY_BACKOFF_SECONDS)
                row.locked_until = utcnow() + timedelta(seconds=backoff)


async def worker_loop(stop: asyncio.Event) -> None:
    log.info("worker started")
    while not stop.is_set():
        try:
            ids = await _claim_batch()
            if ids:
                await asyncio.gather(*(_process_one(i) for i in ids))
                continue  # there may be more due rows; poll again immediately
        except Exception:
            log.exception("worker iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=WORKER_POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("worker stopped")
