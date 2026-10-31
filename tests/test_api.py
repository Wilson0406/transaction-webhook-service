import os
import time

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["PROCESSING_DELAY_SECONDS"] = "2"
os.environ["WORKER_POLL_INTERVAL_SECONDS"] = "0.2"

import pytest
from fastapi.testclient import TestClient

from app.main import app

PAYLOAD = {
    "transaction_id": "txn_abc123def456",
    "source_account": "acc_user_789",
    "destination_account": "acc_merchant_456",
    "amount": 1500,
    "currency": "INR",
}


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test.db"):
        os.remove("test.db")
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/")
    assert r.status_code == 200 and r.json()["status"] == "HEALTHY"


def test_single_transaction_and_duplicates(client):
    start = time.perf_counter()
    r = client.post("/v1/webhooks/transactions", json=PAYLOAD)
    assert r.status_code == 202
    assert time.perf_counter() - start < 0.5

    # Repeated IDs are acknowledged while representing only one transaction.
    for _ in range(3):
        assert client.post("/v1/webhooks/transactions", json=PAYLOAD).status_code == 202

    body = client.get(f"/v1/transactions/{PAYLOAD['transaction_id']}").json()
    assert len(body) == 1 and body[0]["status"] == "PROCESSING" and body[0]["processed_at"] is None

    time.sleep(3.5)  # > 2s test delay
    body = client.get(f"/v1/transactions/{PAYLOAD['transaction_id']}").json()
    assert body[0]["status"] == "PROCESSED" and body[0]["processed_at"] is not None


def test_validation_and_404(client):
    assert client.post("/v1/webhooks/transactions", json={"transaction_id": "x"}).status_code == 422
    assert client.get("/v1/transactions/nope").status_code == 404
