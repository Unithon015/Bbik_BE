from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from src.database import AsyncSessionLocal
from src.infrastructure.db.models import UserModel

DEMO_EMAIL = "demo@bbik.internal"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(UserModel).where(UserModel.email == DEMO_EMAIL))).scalar_one_or_none()
        if existing:
            print(f"이미 존재합니다. DEMO_USER_ID={existing.id}")
            return

        user = UserModel(id=uuid4(), email=DEMO_EMAIL, name="데모", provider="demo", provider_id="demo")
        session.add(user)
        await session.commit()
        print(f"데모 유저 생성 완료. 환경변수에 추가하세요:")
        print(f"DEMO_USER_ID={user.id}")


asyncio.run(main())