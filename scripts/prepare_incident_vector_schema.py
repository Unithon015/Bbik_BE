from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from src.infrastructure.persistence.database import build_engine
from src.infrastructure.policy_catalog.vector import INCIDENT_TABLE_NAME


def prepare_schema() -> None:
    engine = build_engine()
    if engine.dialect.name != "postgresql":
        raise RuntimeError("pgvector schema preparation requires PostgreSQL")
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f"ALTER TABLE {INCIDENT_TABLE_NAME} ADD COLUMN IF NOT EXISTS embedding vector(1536)"))
        connection.execute(text(f"ALTER TABLE {INCIDENT_TABLE_NAME} ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(64)"))


if __name__ == "__main__":
    argparse.ArgumentParser(description="Prepare optional pgvector incident schema.").parse_args()
    prepare_schema()