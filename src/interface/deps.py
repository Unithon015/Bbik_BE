from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.database import get_db
from src.infrastructure.auth.pg_repository import PostgresAuthRepository
from src.infrastructure.user.pg_repository import PostgresUserRepository

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    session_id: UUID


async def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        payload = jwt.decode(
            credentials.credentials,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            audience=config.JWT_AUDIENCE,
            issuer=config.JWT_ISSUER,
        )
        if payload.get("type") != "access":
            raise ValueError("Unexpected token type")
        user_id = UUID(str(payload["sub"]))
        session_id = UUID(str(payload["sid"]))
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise _unauthorized() from exc

    session = await PostgresAuthRepository(db).find_active_session(session_id)
    if not session or session.user_id != user_id:
        raise _unauthorized()
    if not await PostgresUserRepository(db).find_by_id(user_id):
        raise _unauthorized()
    return AuthContext(user_id=user_id, session_id=session_id)


async def get_current_user_id(context: AuthContext = Depends(get_auth_context)) -> UUID:
    return context.user_id


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
    )
