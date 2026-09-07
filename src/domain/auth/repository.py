from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from src.domain.user.entity import User

from .entity import (
    AccountToken,
    AccountTokenPurpose,
    AuthIdentity,
    AuthProvider,
    AuthSession,
)


class AuthRepository(ABC):
    @abstractmethod
    async def create_user_with_identity(
        self, user: User, identity: AuthIdentity
    ) -> User: ...

    @abstractmethod
    async def save_identity(self, identity: AuthIdentity) -> AuthIdentity: ...

    @abstractmethod
    async def find_identity(
        self, provider: AuthProvider, provider_subject: str
    ) -> AuthIdentity | None: ...

    @abstractmethod
    async def update_password(self, identity_id: UUID, password_hash: str) -> None: ...

    @abstractmethod
    async def save_session(self, session: AuthSession) -> AuthSession: ...

    @abstractmethod
    async def find_active_session(self, session_id: UUID) -> AuthSession | None: ...

    @abstractmethod
    async def find_active_session_by_refresh_hash(
        self, refresh_token_hash: str
    ) -> AuthSession | None: ...

    @abstractmethod
    async def rotate_session(
        self, session_id: UUID, refresh_token_hash: str, expires_at: datetime
    ) -> AuthSession: ...

    @abstractmethod
    async def revoke_session_by_refresh_hash(self, refresh_token_hash: str) -> None: ...

    @abstractmethod
    async def revoke_all_sessions(self, user_id: UUID) -> None: ...

    @abstractmethod
    async def save_account_token(self, token: AccountToken) -> AccountToken: ...

    @abstractmethod
    async def reset_password(
        self, token_hash: str, purpose: AccountTokenPurpose, password_hash: str
    ) -> UUID | None: ...

    @abstractmethod
    async def delete_user(self, user_id: UUID) -> list[str]: ...
