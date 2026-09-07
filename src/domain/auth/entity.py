from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class AuthProvider(StrEnum):
    LOCAL = "local"
    GOOGLE = "google"


class AccountTokenPurpose(StrEnum):
    RESET_PASSWORD = "RESET_PASSWORD"


@dataclass(frozen=True)
class AuthIdentity:
    user_id: UUID
    provider: AuthProvider
    provider_subject: str
    password_hash: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AuthSession:
    user_id: UUID
    refresh_token_hash: str
    expires_at: datetime
    user_agent: str | None = None
    revoked_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AccountToken:
    user_id: UUID
    purpose: AccountTokenPurpose
    token_hash: str
    expires_at: datetime
    used_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class AuthResult:
    user_id: UUID
    email: str
    name: str
    tokens: AuthTokens


@dataclass(frozen=True)
class PasswordResetDelivery:
    recipient: str
    reset_url: str
