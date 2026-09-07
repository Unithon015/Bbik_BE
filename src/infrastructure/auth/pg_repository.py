from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.auth.entity import (
    AccountToken,
    AccountTokenPurpose,
    AuthIdentity,
    AuthProvider,
    AuthSession,
)
from src.domain.auth.repository import AuthRepository
from src.domain.user.entity import User
from src.infrastructure.db.models import (
    AccountTokenModel,
    AuthIdentityModel,
    AuthSessionModel,
    ContentAssetModel,
    ContentSubmissionModel,
    UserModel,
)


class PostgresAuthRepository(AuthRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_user_with_identity(
        self, user: User, identity: AuthIdentity
    ) -> User:
        self._session.add(
            UserModel(
                id=user.id,
                email=user.email,
                name=user.name,
                provider=user.provider,
                provider_id=user.provider_id,
            )
        )
        self._session.add(
            AuthIdentityModel(
                id=identity.id,
                user_id=user.id,
                provider=identity.provider.value,
                provider_subject=identity.provider_subject,
                password_hash=identity.password_hash,
                created_at=identity.created_at,
            )
        )
        await self._session.commit()
        return user

    async def save_identity(self, identity: AuthIdentity) -> AuthIdentity:
        self._session.add(
            AuthIdentityModel(
                id=identity.id,
                user_id=identity.user_id,
                provider=identity.provider.value,
                provider_subject=identity.provider_subject,
                password_hash=identity.password_hash,
                created_at=identity.created_at,
            )
        )
        await self._session.commit()
        return identity

    async def find_identity(
        self, provider: AuthProvider, provider_subject: str
    ) -> AuthIdentity | None:
        result = await self._session.execute(
            select(AuthIdentityModel).where(
                AuthIdentityModel.provider == provider.value,
                AuthIdentityModel.provider_subject == provider_subject,
            )
        )
        model = result.scalar_one_or_none()
        return self._identity(model) if model else None

    async def update_password(self, identity_id: UUID, password_hash: str) -> None:
        result = await self._session.execute(
            select(AuthIdentityModel).where(AuthIdentityModel.id == identity_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            raise LookupError("Local identity not found")
        model.password_hash = password_hash
        await self._session.commit()

    async def save_session(self, session: AuthSession) -> AuthSession:
        self._session.add(
            AuthSessionModel(
                id=session.id,
                user_id=session.user_id,
                refresh_token_hash=session.refresh_token_hash,
                expires_at=session.expires_at,
                revoked_at=session.revoked_at,
                user_agent=session.user_agent,
                created_at=session.created_at,
                updated_at=session.updated_at,
            )
        )
        await self._session.commit()
        return session

    async def find_active_session(self, session_id: UUID) -> AuthSession | None:
        result = await self._session.execute(
            select(AuthSessionModel).where(
                AuthSessionModel.id == session_id,
                AuthSessionModel.revoked_at.is_(None),
                AuthSessionModel.expires_at > datetime.now(timezone.utc),
            )
        )
        model = result.scalar_one_or_none()
        return self._auth_session(model) if model else None

    async def find_active_session_by_refresh_hash(
        self, refresh_token_hash: str
    ) -> AuthSession | None:
        result = await self._session.execute(
            select(AuthSessionModel)
            .where(
                AuthSessionModel.refresh_token_hash == refresh_token_hash,
                AuthSessionModel.revoked_at.is_(None),
                AuthSessionModel.expires_at > datetime.now(timezone.utc),
            )
            .with_for_update()
        )
        model = result.scalar_one_or_none()
        return self._auth_session(model) if model else None

    async def rotate_session(
        self, session_id: UUID, refresh_token_hash: str, expires_at: datetime
    ) -> AuthSession:
        result = await self._session.execute(
            select(AuthSessionModel).where(
                AuthSessionModel.id == session_id,
                AuthSessionModel.revoked_at.is_(None),
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            raise LookupError("Active session not found")
        model.refresh_token_hash = refresh_token_hash
        model.expires_at = expires_at
        model.updated_at = datetime.now(timezone.utc)
        await self._session.commit()
        return self._auth_session(model)

    async def revoke_session_by_refresh_hash(self, refresh_token_hash: str) -> None:
        await self._session.execute(
            update(AuthSessionModel)
            .where(
                AuthSessionModel.refresh_token_hash == refresh_token_hash,
                AuthSessionModel.revoked_at.is_(None),
            )
            .values(
                revoked_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.commit()

    async def revoke_all_sessions(self, user_id: UUID) -> None:
        await self._session.execute(
            update(AuthSessionModel)
            .where(
                AuthSessionModel.user_id == user_id,
                AuthSessionModel.revoked_at.is_(None),
            )
            .values(
                revoked_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.commit()

    async def save_account_token(self, token: AccountToken) -> AccountToken:
        await self._session.execute(
            update(AccountTokenModel)
            .where(
                AccountTokenModel.user_id == token.user_id,
                AccountTokenModel.purpose == token.purpose.value,
                AccountTokenModel.used_at.is_(None),
            )
            .values(used_at=datetime.now(timezone.utc))
        )
        self._session.add(
            AccountTokenModel(
                id=token.id,
                user_id=token.user_id,
                purpose=token.purpose.value,
                token_hash=token.token_hash,
                expires_at=token.expires_at,
                used_at=token.used_at,
                created_at=token.created_at,
            )
        )
        await self._session.commit()
        return token

    async def reset_password(
        self, token_hash: str, purpose: AccountTokenPurpose, password_hash: str
    ) -> UUID | None:
        result = await self._session.execute(
            select(AccountTokenModel)
            .where(
                AccountTokenModel.token_hash == token_hash,
                AccountTokenModel.purpose == purpose.value,
                AccountTokenModel.used_at.is_(None),
                AccountTokenModel.expires_at > datetime.now(timezone.utc),
            )
            .with_for_update()
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        user_result = await self._session.execute(
            select(UserModel).where(UserModel.id == model.user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            return None
        identity_result = await self._session.execute(
            select(AuthIdentityModel).where(
                AuthIdentityModel.provider == AuthProvider.LOCAL.value,
                AuthIdentityModel.provider_subject == user.email.lower(),
            )
        )
        identity = identity_result.scalar_one_or_none()
        if not identity:
            return None

        model.used_at = datetime.now(timezone.utc)
        identity.password_hash = password_hash
        await self._session.execute(
            update(AuthSessionModel)
            .where(
                AuthSessionModel.user_id == user.id,
                AuthSessionModel.revoked_at.is_(None),
            )
            .values(
                revoked_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self._session.commit()
        return user.id

    async def delete_user(self, user_id: UUID) -> list[str]:
        assets = await self._session.execute(
            select(ContentAssetModel.storage_key)
            .join(
                ContentSubmissionModel,
                ContentSubmissionModel.id == ContentAssetModel.submission_id,
            )
            .where(ContentSubmissionModel.owner_id == user_id)
        )
        storage_keys = list(assets.scalars().all())
        result = await self._session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise LookupError("User not found")
        await self._session.delete(user)
        await self._session.commit()
        return storage_keys

    @staticmethod
    def _identity(model: AuthIdentityModel) -> AuthIdentity:
        return AuthIdentity(
            id=model.id,
            user_id=model.user_id,
            provider=AuthProvider(model.provider),
            provider_subject=model.provider_subject,
            password_hash=model.password_hash,
            created_at=model.created_at,
        )

    @staticmethod
    def _auth_session(model: AuthSessionModel) -> AuthSession:
        return AuthSession(
            id=model.id,
            user_id=model.user_id,
            refresh_token_hash=model.refresh_token_hash,
            expires_at=model.expires_at,
            revoked_at=model.revoked_at,
            user_agent=model.user_agent,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
