import logging
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID, uuid4

from jose import jwt

from src import config
from src.domain.auth.entity import (
    AccountToken,
    AccountTokenPurpose,
    AuthIdentity,
    AuthProvider,
    AuthResult,
    AuthSession,
    AuthTokens,
    PasswordResetDelivery,
)
from src.domain.auth.repository import AuthRepository
from src.domain.user.entity import User
from src.domain.user.repository import UserRepository
from src.infrastructure.google import client as google

logger = logging.getLogger(__name__)


class PasswordHashing(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password_hash: str | None, password: str) -> bool: ...
    def needs_rehash(self, password_hash: str) -> bool: ...


class PasswordResetEmailSender(Protocol):
    @property
    def is_configured(self) -> bool: ...
    async def send_password_reset(self, recipient: str, reset_url: str) -> None: ...


class DuplicateEmailError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class InvalidRefreshTokenError(ValueError):
    pass


class InvalidPasswordResetTokenError(ValueError):
    pass


class EmailDeliveryNotConfiguredError(RuntimeError):
    pass


class GoogleEmailNotVerifiedError(ValueError):
    pass


class AuthenticationService:
    def __init__(
        self,
        users: UserRepository,
        auth: AuthRepository,
        passwords: PasswordHashing,
        email_sender: PasswordResetEmailSender,
    ):
        self._users = users
        self._auth = auth
        self._passwords = passwords
        self._email_sender = email_sender

    async def signup(
        self, *, email: str, password: str, name: str, user_agent: str | None = None
    ) -> AuthResult:
        normalized_email = _normalize_email(email)
        if await self._users.find_by_email(normalized_email):
            raise DuplicateEmailError("이미 가입된 이메일입니다.")

        user = User(
            email=normalized_email,
            name=name.strip(),
            provider=AuthProvider.LOCAL.value,
            provider_id=normalized_email,
        )
        identity = AuthIdentity(
            user_id=user.id,
            provider=AuthProvider.LOCAL,
            provider_subject=normalized_email,
            password_hash=self._passwords.hash(password),
        )
        user = await self._auth.create_user_with_identity(user, identity)
        return await self._create_session(user, user_agent)

    async def login_with_password(
        self, *, email: str, password: str, user_agent: str | None = None
    ) -> AuthResult:
        normalized_email = _normalize_email(email)
        user = await self._users.find_by_email(normalized_email)
        identity = await self._auth.find_identity(AuthProvider.LOCAL, normalized_email)
        password_hash = identity.password_hash if identity else None
        if (
            not self._passwords.verify(password_hash, password)
            or not user
            or not identity
            or identity.user_id != user.id
        ):
            raise InvalidCredentialsError("이메일 또는 비밀번호가 올바르지 않습니다.")

        if password_hash and self._passwords.needs_rehash(password_hash):
            await self._auth.update_password(
                identity.id, self._passwords.hash(password)
            )
        return await self._create_session(user, user_agent)

    async def login(self, code: str, user_agent: str | None = None) -> AuthResult:
        """Complete Google OAuth login and issue the session used by local login."""
        tokens = await google.exchange_code(code)
        user_info = await google.get_user_info(tokens["access_token"])
        if user_info.get("email_verified") not in {True, "true", "True"}:
            raise GoogleEmailNotVerifiedError(
                "Google 이메일 인증을 확인할 수 없습니다."
            )

        subject = str(user_info["sub"])
        email = _normalize_email(str(user_info["email"]))
        identity = await self._auth.find_identity(AuthProvider.GOOGLE, subject)
        user = await self._users.find_by_id(identity.user_id) if identity else None

        if not user:
            user = await self._users.find_by_email(email)
            if not user:
                new_user = User(
                    email=email,
                    name=str(user_info.get("name") or email),
                    provider=AuthProvider.GOOGLE.value,
                    provider_id=subject,
                )
                identity = AuthIdentity(
                    user_id=new_user.id,
                    provider=AuthProvider.GOOGLE,
                    provider_subject=subject,
                )
                user = await self._auth.create_user_with_identity(new_user, identity)
            elif not identity:
                await self._auth.save_identity(
                    AuthIdentity(
                        user_id=user.id,
                        provider=AuthProvider.GOOGLE,
                        provider_subject=subject,
                    )
                )
        return await self._create_session(user, user_agent)

    async def refresh(self, refresh_token: str) -> AuthResult:
        session = await self._auth.find_active_session_by_refresh_hash(
            _token_hash(refresh_token)
        )
        if not session:
            raise InvalidRefreshTokenError("유효하지 않은 로그인 세션입니다.")
        user = await self._users.find_by_id(session.user_id)
        if not user:
            raise InvalidRefreshTokenError("유효하지 않은 로그인 세션입니다.")

        new_refresh_token = token_urlsafe(48)
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=config.REFRESH_TOKEN_EXPIRE_DAYS
        )
        session = await self._auth.rotate_session(
            session.id, _token_hash(new_refresh_token), expires_at
        )
        return self._result(user, session, new_refresh_token)

    async def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            await self._auth.revoke_session_by_refresh_hash(_token_hash(refresh_token))

    async def create_password_reset(self, email: str) -> PasswordResetDelivery | None:
        if not self._email_sender.is_configured:
            raise EmailDeliveryNotConfiguredError(
                "비밀번호 재설정 메일이 설정되지 않았습니다."
            )

        normalized_email = _normalize_email(email)
        user = await self._users.find_by_email(normalized_email)
        identity = await self._auth.find_identity(AuthProvider.LOCAL, normalized_email)
        if not user or not identity:
            return None

        raw_token = token_urlsafe(48)
        await self._auth.save_account_token(
            AccountToken(
                user_id=user.id,
                purpose=AccountTokenPurpose.RESET_PASSWORD,
                token_hash=_token_hash(raw_token),
                expires_at=datetime.now(timezone.utc)
                + timedelta(minutes=config.PASSWORD_RESET_EXPIRE_MINUTES),
            )
        )
        separator = "&" if "?" in config.PASSWORD_RESET_URL else "?"
        return PasswordResetDelivery(
            recipient=user.email,
            reset_url=f"{config.PASSWORD_RESET_URL}{separator}{urlencode({'token': raw_token})}",
        )

    async def deliver_password_reset(self, delivery: PasswordResetDelivery) -> None:
        try:
            await self._email_sender.send_password_reset(
                delivery.recipient, delivery.reset_url
            )
        except Exception:
            logger.exception("Failed to deliver password reset email")

    async def reset_password(self, raw_token: str, new_password: str) -> None:
        user_id = await self._auth.reset_password(
            _token_hash(raw_token),
            AccountTokenPurpose.RESET_PASSWORD,
            self._passwords.hash(new_password),
        )
        if not user_id:
            raise InvalidPasswordResetTokenError(
                "유효하지 않거나 만료된 재설정 링크입니다."
            )

    async def delete_account(self, user_id: UUID, password: str | None) -> list[str]:
        user = await self._users.find_by_id(user_id)
        if not user:
            raise InvalidCredentialsError("인증 정보를 확인할 수 없습니다.")
        identity = await self._auth.find_identity(
            AuthProvider.LOCAL, _normalize_email(user.email)
        )
        if identity and not self._passwords.verify(
            identity.password_hash, password or ""
        ):
            raise InvalidCredentialsError("현재 비밀번호가 올바르지 않습니다.")
        await self._auth.revoke_all_sessions(user_id)
        return await self._auth.delete_user(user_id)

    async def _create_session(self, user: User, user_agent: str | None) -> AuthResult:
        refresh_token = token_urlsafe(48)
        session = AuthSession(
            user_id=user.id,
            refresh_token_hash=_token_hash(refresh_token),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS),
            user_agent=(user_agent or "")[:512] or None,
        )
        await self._auth.save_session(session)
        return self._result(user, session, refresh_token)

    def _result(
        self, user: User, session: AuthSession, refresh_token: str
    ) -> AuthResult:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = jwt.encode(
            {
                "sub": str(user.id),
                "sid": str(session.id),
                "email": user.email,
                "type": "access",
                "iss": config.JWT_ISSUER,
                "aud": config.JWT_AUDIENCE,
                "iat": now,
                "exp": expires_at,
                "jti": str(uuid4()),
            },
            config.JWT_SECRET,
            algorithm=config.JWT_ALGORITHM,
        )
        return AuthResult(
            user_id=user.id,
            email=user.email,
            name=user.name,
            tokens=AuthTokens(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            ),
        )


# Compatibility name for existing imports.
GoogleAuthService = AuthenticationService


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()
