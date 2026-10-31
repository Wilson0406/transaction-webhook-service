import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./local.db")

PROCESSING_DELAY_SECONDS = float(os.getenv("PROCESSING_DELAY_SECONDS", "30"))

WORKER_POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1"))
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "50"))
LEASE_SECONDS = float(os.getenv("LEASE_SECONDS", "60"))
MAX_RETRY_BACKOFF_SECONDS = 60
