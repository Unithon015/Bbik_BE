import unittest
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.application.user.dto import UserResponse
from src.interface.deps import get_current_user_id
from src.interface.user import router as router_module


class FakeUserService:
    async def get(self, user_id):
        return UserResponse(id=user_id, email="creator@example.com", name="Creator")


class FakeAuthService:
    def __init__(self):
        self.deleted = None

    async def delete_account(self, user_id, password):
        self.deleted = (user_id, password)
        return []


class UserRouterTest(unittest.TestCase):
    def setUp(self):
        self.user_id = UUID("00000000-0000-0000-0000-000000000001")
        self.auth = FakeAuthService()
        app = FastAPI()
        app.include_router(router_module.router)
        app.dependency_overrides[get_current_user_id] = lambda: self.user_id
        app.dependency_overrides[router_module._service] = FakeUserService
        app.dependency_overrides[router_module._auth_service] = lambda: self.auth
        self.client = TestClient(app)

    def test_deletes_the_current_account_and_clears_refresh_cookie(self):
        self.client.cookies.set("refresh_token", "refresh-token", path="/auth")
        response = self.client.request(
            "DELETE", "/users/me", json={"password": "current password"}
        )

        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.auth.deleted, (self.user_id, "current password"))
        self.assertIn("refresh_token=", response.headers["set-cookie"])
