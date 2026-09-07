from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.auth.service import AuthenticationService
from src.database import get_db
from src.infrastructure.auth.pg_repository import PostgresAuthRepository
from src.infrastructure.email.ses import SesEmailSender
from src.infrastructure.security.password_hasher import PasswordHasher
from src.infrastructure.user.pg_repository import PostgresUserRepository

_password_hasher = PasswordHasher()
_email_sender = SesEmailSender()


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthenticationService:
    return AuthenticationService(
        PostgresUserRepository(db),
        PostgresAuthRepository(db),
        _password_hasher,
        _email_sender,
    )
