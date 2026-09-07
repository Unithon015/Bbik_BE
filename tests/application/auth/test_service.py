import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from jose import jwt

from src import config
from src.application.auth.service import (
    AuthenticationService,
    GoogleEmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidPasswordResetTokenError,
    InvalidRefreshTokenError,
)
from src.domain.auth.entity import AuthIdentity, AuthProvider, AuthSession
from src.domain.auth.repository import AuthRepository
from src.domain.user.entity import User
from src.domain.user.repository import UserRepository


class InMemoryUserRepository(UserRepository):
    def __init__(self):
        self.users: dict[UUID, User] = {}

    async def save(self, user: User) -> User:
        self.users[user.id] = user
        return user

    async def find_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def find_by_email(self, email: str) -> User | None:
        return next((user for user in self.users.values() if user.email == email), None)

    async def find_all(self) -> list[User]:
        return list(self.users.values())


class InMemoryAuthRepository(AuthRepository):
    def __init__(self, users: InMemoryUserRepository):
        self.users = users
        self.identities: dict[tuple[AuthProvider, str], AuthIdentity] = {}
        self.sessions: dict[UUID, AuthSession] = {}
        self.tokens = {}

    async def create_user_with_identity(self, user, identity):
        await self.users.save(user)
        await self.save_identity(identity)
        return user

    async def save_identity(self, identity):
        self.identities[(identity.provider, identity.provider_subject)] = identity
        return identity

    async def find_identity(self, provider, provider_subject):
        return self.identities.get((provider, provider_subject))

    async def update_password(self, identity_id, password_hash):
        for key, identity in self.identities.items():
            if identity.id == identity_id:
                self.identities[key] = AuthIdentity(
                    id=identity.id,
                    user_id=identity.user_id,
                    provider=identity.provider,
                    provider_subject=identity.provider_subject,
                    password_hash=password_hash,
                    created_at=identity.created_at,
                )
                return
        raise LookupError

    async def save_session(self, session):
        self.sessions[session.id] = session
        return session

    async def find_active_session(self, session_id):
        session = self.sessions.get(session_id)
        if (
            session
            and not session.revoked_at
            and session.expires_at > datetime.now(timezone.utc)
        ):
            return session
        return None

    async def find_active_session_by_refresh_hash(self, refresh_token_hash):
        for session in self.sessions.values():
            if session.refresh_token_hash == refresh_token_hash:
                return await self.find_active_session(session.id)
        return None

    async def rotate_session(self, session_id, refresh_token_hash, expires_at):
        old = self.sessions[session_id]
        session = AuthSession(
            id=old.id,
            user_id=old.user_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            user_agent=old.user_agent,
            created_at=old.created_at,
        )
        self.sessions[session_id] = session
        return session

    async def revoke_session_by_refresh_hash(self, refresh_token_hash):
        for session_id, session in self.sessions.items():
            if session.refresh_token_hash == refresh_token_hash:
                self.sessions[session_id] = AuthSession(
                    id=session.id,
                    user_id=session.user_id,
                    refresh_token_hash=session.refresh_token_hash,
                    expires_at=session.expires_at,
                    user_agent=session.user_agent,
                    revoked_at=datetime.now(timezone.utc),
                    created_at=session.created_at,
                )

    async def revoke_all_sessions(self, user_id):
        for session in list(self.sessions.values()):
            if session.user_id == user_id:
                await self.revoke_session_by_refresh_hash(session.refresh_token_hash)

    async def save_account_token(self, token):
        for current_hash, current in list(self.tokens.items()):
            if current.user_id == token.user_id and current.purpose == token.purpose:
                del self.tokens[current_hash]
        self.tokens[token.token_hash] = token
        return token

    async def reset_password(self, token_hash, purpose, password_hash):
        token = self.tokens.pop(token_hash, None)
        if (
            not token
            or token.purpose != purpose
            or token.expires_at <= datetime.now(timezone.utc)
        ):
            return None
        user = await self.users.find_by_id(token.user_id)
        if not user:
            return None
        identity = await self.find_identity(AuthProvider.LOCAL, user.email)
        if not identity:
            return None
        await self.update_password(identity.id, password_hash)
        await self.revoke_all_sessions(user.id)
        return user.id

    async def delete_user(self, user_id):
        self.users.users.pop(user_id, None)
        return ["content/key.png"]


class FakePasswordHasher:
    def hash(self, password):
        return f"hashed:{password}"

    def verify(self, password_hash, password):
        return password_hash == self.hash(password)

    def needs_rehash(self, password_hash):
        return False


class FakeEmailSender:
    is_configured = True

    def __init__(self):
        self.sent = []

    async def send_password_reset(self, recipient, reset_url):
        self.sent.append((recipient, reset_url))


class AuthenticationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.users = InMemoryUserRepository()
        self.auth = InMemoryAuthRepository(self.users)
        self.email = FakeEmailSender()
        self.service = AuthenticationService(
            self.users, self.auth, FakePasswordHasher(), self.email
        )

    async def test_signup_login_refresh_and_logout(self):
        signup = await self.service.signup(
            email=" Creator@Example.com ",
            password="a sufficiently long password",
            name="Creator",
        )
        self.assertEqual(signup.email, "creator@example.com")
        claims = jwt.decode(
            signup.tokens.access_token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
            audience=config.JWT_AUDIENCE,
            issuer=config.JWT_ISSUER,
        )
        self.assertEqual(claims["sub"], str(signup.user_id))
        self.assertEqual(claims["type"], "access")
        self.assertTrue(claims["sid"])

        with self.assertRaises(InvalidCredentialsError):
            await self.service.login_with_password(
                email="creator@example.com", password="wrong"
            )

        login = await self.service.login_with_password(
            email="creator@example.com", password="a sufficiently long password"
        )
        refreshed = await self.service.refresh(login.tokens.refresh_token)
        with self.assertRaises(InvalidRefreshTokenError):
            await self.service.refresh(login.tokens.refresh_token)

        await self.service.logout(refreshed.tokens.refresh_token)
        with self.assertRaises(InvalidRefreshTokenError):
            await self.service.refresh(refreshed.tokens.refresh_token)

    async def test_password_reset_is_single_use_and_revokes_sessions(self):
        signup = await self.service.signup(
            email="creator@example.com",
            password="a sufficiently long password",
            name="Creator",
        )
        delivery = await self.service.create_password_reset("creator@example.com")
        self.assertIsNotNone(delivery)
        raw_token = parse_qs(urlparse(delivery.reset_url).query)["token"][0]

        await self.service.reset_password(raw_token, "an entirely new long password")
        with self.assertRaises(InvalidPasswordResetTokenError):
            await self.service.reset_password(
                raw_token, "another sufficiently long password"
            )
        with self.assertRaises(InvalidRefreshTokenError):
            await self.service.refresh(signup.tokens.refresh_token)

        await self.service.login_with_password(
            email="creator@example.com", password="an entirely new long password"
        )

    async def test_unknown_password_reset_has_the_same_empty_delivery(self):
        self.assertIsNone(
            await self.service.create_password_reset("missing@example.com")
        )

    async def test_local_account_deletion_requires_current_password(self):
        signup = await self.service.signup(
            email="creator@example.com",
            password="a sufficiently long password",
            name="Creator",
        )
        with self.assertRaises(InvalidCredentialsError):
            await self.service.delete_account(signup.user_id, "wrong")

        keys = await self.service.delete_account(
            signup.user_id, "a sufficiently long password"
        )
        self.assertEqual(keys, ["content/key.png"])
        self.assertIsNone(await self.users.find_by_id(signup.user_id))

    async def test_google_login_creates_a_shared_auth_session(self):
        with (
            patch(
                "src.application.auth.service.google.exchange_code",
                new=AsyncMock(return_value={"access_token": "google-access"}),
            ),
            patch(
                "src.application.auth.service.google.get_user_info",
                new=AsyncMock(
                    return_value={
                        "sub": "google-subject",
                        "email": "Creator@Example.com",
                        "email_verified": True,
                        "name": "Creator",
                    }
                ),
            ),
        ):
            result = await self.service.login("authorization-code")

        self.assertEqual(result.email, "creator@example.com")
        identity = await self.auth.find_identity(AuthProvider.GOOGLE, "google-subject")
        self.assertEqual(identity.user_id, result.user_id)
        self.assertTrue(result.tokens.refresh_token)

    async def test_google_login_rejects_an_unverified_email(self):
        with (
            patch(
                "src.application.auth.service.google.exchange_code",
                new=AsyncMock(return_value={"access_token": "google-access"}),
            ),
            patch(
                "src.application.auth.service.google.get_user_info",
                new=AsyncMock(
                    return_value={
                        "sub": "google-subject",
                        "email": "creator@example.com",
                        "email_verified": False,
                    }
                ),
            ),
        ):
            with self.assertRaises(GoogleEmailNotVerifiedError):
                await self.service.login("authorization-code")
