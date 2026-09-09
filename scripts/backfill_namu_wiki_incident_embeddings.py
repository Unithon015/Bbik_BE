from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import config
from src.application.incident_index.embedding_backfill import (
    DEFAULT_BATCH_SIZE,
    backfill_missing_incident_embeddings,
)
from src.infrastructure.persistence.database import build_engine

BATCH_SIZE = DEFAULT_BATCH_SIZE


def backfill_missing_embeddings(batch_size: int = BATCH_SIZE) -> int:
    api_key = config.OPEN_API_KEY
    if not api_key:
        raise RuntimeError("OPEN_API_KEY must be configured for embedding backfill")
    return asyncio.run(
        backfill_missing_incident_embeddings(
            batch_size=batch_size,
            api_key=api_key,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing NamuWiki incident embeddings.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    arguments = parser.parse_args()
    print(f"updated={backfill_missing_embeddings(arguments.batch_size)}")
