import unittest
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config
from src.application.auth.service import InvalidRefreshTokenError
from src.domain.auth.entity import AuthResult, AuthTokens
from src.interface.auth import router as router_module


def _result(access_token="access-token", refresh_token="refresh-token"):
    return AuthResult(
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        email="creator@example.com",
        name="Creator",
        tokens=AuthTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=900,
        ),
    )


class _FakeAuthService:
    def __init__(self):
        self.logged_out = None
        self.reset_token = None

    async def signup(self, **kwargs):
        return _result()

    async def login_with_password(self, **kwargs):
        return _result()

    async def refresh(self, refresh_token):
        if refresh_token == "invalid-refresh-token":
            raise InvalidRefreshTokenError("유효하지 않은 로그인 세션입니다.")
        return _result("new-access-token", "new-refresh-token")

    async def logout(self, refresh_token):
        self.logged_out = refresh_token

    async def create_password_reset(self, email):
        return None

    async def deliver_password_reset(self, delivery):
        return None

    async def reset_password(self, token, new_password):
        self.reset_token = token

    async def login(self, code, user_agent=None):
        return _result(f"token-for-{code}", "google-refresh-token")


class AuthRouterTest(unittest.TestCase):
    def setUp(self):
        self.original_environment = config.ENVIRONMENT
        self.original_frontend_url = config.FRONTEND_URL
        self.original_google_auth_success_url = config.GOOGLE_AUTH_SUCCESS_URL
        config.ENVIRONMENT = "development"
        config.FRONTEND_URL = "http://localhost:5173"
        config.GOOGLE_AUTH_SUCCESS_URL = "http://localhost:5173/auth/callback"
        self.service = _FakeAuthService()
        app = FastAPI()
        app.include_router(router_module.router)
        app.dependency_overrides[router_module._service] = lambda: self.service
        self.client = TestClient(app)

    def tearDown(self):
        config.ENVIRONMENT = self.original_environment
        config.FRONTEND_URL = self.original_frontend_url
        config.GOOGLE_AUTH_SUCCESS_URL = self.original_google_auth_success_url

    def test_signup_returns_access_token_and_sets_refresh_cookie(self):
        response = self.client.post(
            "/auth/signup",
            json={
                "email": "creator@example.com",
                "password": "a sufficiently long password",
                "name": "Creator",
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["access_token"], "access-token")
        self.assertEqual(response.cookies["refresh_token"], "refresh-token")
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    def test_login_refresh_and_logout_rotate_the_cookie(self):
        login = self.client.post(
            "/auth/login",
            json={"email": "creator@example.com", "password": "password"},
        )
        self.assertEqual(login.status_code, 200)

        refresh = self.client.post("/auth/refresh")
        self.assertEqual(refresh.status_code, 200, refresh.text)
        self.assertEqual(refresh.json()["access_token"], "new-access-token")
        self.assertEqual(refresh.cookies["refresh_token"], "new-refresh-token")

        logout = self.client.post("/auth/logout")
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.service.logged_out, "new-refresh-token")
        self.assertIn("refresh_token=", logout.headers["set-cookie"])

    def test_password_reset_request_does_not_reveal_account_existence(self):
        response = self.client.post(
            "/auth/password/forgot", json={"email": "missing@example.com"}
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json()["message"],
            "가입된 이메일이라면 비밀번호 재설정 안내를 발송했습니다.",
        )

    def test_invalid_refresh_token_is_removed(self):
        self.client.cookies.set("refresh_token", "invalid-refresh-token", path="/auth")
        response = self.client.post("/auth/refresh")

        self.assertEqual(response.status_code, 401)
        self.assertIn("refresh_token=", response.headers["set-cookie"])

    def test_rejects_a_google_callback_without_a_matching_state(self):
        response = self.client.get(
            "/auth/google/callback?code=authorization-code&state=wrong",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 400)

    def test_google_callback_sets_refresh_cookie_without_token_in_url(self):
        login = self.client.get("/auth/google/login", follow_redirects=False)
        state_value = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        callback = self.client.get(
            f"/auth/google/callback?code=authorization-code&state={state_value}",
            follow_redirects=False,
        )

        self.assertEqual(callback.status_code, 307)
        self.assertEqual(
            callback.headers["location"], "http://localhost:5173/auth/callback"
        )
        self.assertNotIn("token=", callback.headers["location"])
        self.assertEqual(callback.cookies["refresh_token"], "google-refresh-token")
