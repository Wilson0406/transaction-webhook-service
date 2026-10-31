# Transaction Webhook Service

FastAPI service that accepts payment-processor webhooks, acknowledges them immediately
with `202 Accepted`, and processes each transaction in the background after a 30-second
simulated delay. Results are stored in Postgres (Supabase). Includes a small static frontend
for sending webhooks and watching their status.

**Live API:** `https://transaction-webhook-service-6mpq.onrender.com`
**Live frontend:** `https://confluencr-webhook-fe.netlify.app`

```
frontend/ (static)  ->  app/ FastAPI + background worker  ->  Supabase Postgres
```

More: [`docs/DEPLOY.md`](docs/DEPLOY.md) (AWS / Render / Netlify) · [`docs/INTERVIEW_PREP.md`](docs/INTERVIEW_PREP.md)

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check → `{"status":"HEALTHY","current_time":"..."}` |
| POST | `/v1/webhooks/transactions` | Accept a webhook → `202 Accepted` |
| GET | `/v1/transactions/{transaction_id}` | Status lookup (`PROCESSING` / `PROCESSED`) |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Optional: use Supabase Postgres (Settings -> Database -> pooler connection string).
# Without this it falls back to a local SQLite file.
export DATABASE_URL="postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres"

uvicorn app.main:app --reload
```

Frontend: open `frontend/index.html` in a browser (or `python -m http.server -d frontend 3000`)
and set the API base URL to `http://localhost:8000`.

## Test it

Automated (uses a 2s delay and SQLite):
```bash
pytest -q
```

Manually:
```bash
BASE=http://localhost:8000   # or your deployed URL

# 1. Single transaction
curl -i -X POST $BASE/v1/webhooks/transactions -H 'Content-Type: application/json' -d '{
  "transaction_id":"txn_abc123def456","source_account":"acc_user_789",
  "destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'

curl $BASE/v1/transactions/txn_abc123def456   # PROCESSING, processed_at null
sleep 31
curl $BASE/v1/transactions/txn_abc123def456   # PROCESSED, processed_at ≈ created_at + 30s

# 2. Duplicates: send the same POST several times -> always 202, still exactly one row
```

## Technical choices

- **FastAPI + async SQLAlchemy + asyncpg**: the webhook handler does one indexed INSERT and returns, so it stays far below the 500ms budget regardless of processing load.
- **Idempotency via a PRIMARY KEY on `transaction_id`**: duplicate webhooks hit a unique violation, which is caught and acknowledged with `202`. This is race-free even with concurrent duplicates or multiple instances, unlike a "check then insert" approach.
- **The database is the job queue** (no Redis/Celery needed): each row stores `process_after = now + 30s`. A background worker claims due rows with `SELECT ... FOR UPDATE SKIP LOCKED`, sets a lease (`locked_until`), then marks them `PROCESSED`. This means:
  - **No lost transactions**: the transaction is durably stored *before* we return 202; restarts/crashes can't drop pending work.
  - **No blocked workers**: the 30s delay is a scheduled time, not a `sleep`, so thousands of in-flight transactions cost nothing.
  - **Crash recovery**: if a worker dies mid-job, the lease expires and the row is retried; failures use exponential backoff.
  - **Horizontally scalable**: multiple instances can run safely; the final update is conditional on `status = PROCESSING`, so a transaction completes at most once.
- **Postgres (Supabase)**: managed, durable, supports `SKIP LOCKED`; the code also works with the Supabase pooler (prepared statements disabled for pgbouncer). SQLite is supported only as a zero-setup local fallback.
- **Fargate over App Runner**: the worker runs in-process and needs CPU while idle, which App Runner throttles.
- **Validation**: Pydantic rejects bad payloads (missing fields, non-positive amount, bad currency) with `422`. Amounts are stored as `NUMERIC(18,2)`, not floats.

## Notes / trade-offs

- Free-tier hosts may cold-start after inactivity; hit `/` once before testing to warm it up so the first webhook meets the 500ms target.
- With a real payment processor you'd also verify a webhook signature header (e.g. Razorpay's `X-Razorpay-Signature`); omitted here as it isn't in the spec.
