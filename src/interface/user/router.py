import logging
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src import config
from src.application.auth.dto import DeleteAccountRequest
from src.application.auth.service import AuthenticationService, InvalidCredentialsError
from src.application.user.dto import UserResponse
from src.application.user.service import UserService
from src.database import get_db
from src.infrastructure.user.pg_repository import PostgresUserRepository
from src.interface.auth.dependencies import get_auth_service
from src.interface.deps import get_current_user_id

router = APIRouter(prefix="/users", tags=["users"])
logger = logging.getLogger(__name__)


def _service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(PostgresUserRepository(db))


def _auth_service(
    service: AuthenticationService = Depends(get_auth_service),
) -> AuthenticationService:
    return service


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    current_user_id: UUID = Depends(get_current_user_id),
    service: UserService = Depends(_service),
):
    user = await service.get(current_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(
    body: DeleteAccountRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    current_user_id: UUID = Depends(get_current_user_id),
    service: AuthenticationService = Depends(_auth_service),
) -> None:
    try:
        storage_keys = await service.delete_account(current_user_id, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    response.delete_cookie(
        "refresh_token",
        path="/auth",
        httponly=True,
        secure=config.ENVIRONMENT in {"production", "prod"},
        samesite="lax",
    )
    if storage_keys:
        background_tasks.add_task(_delete_storage_objects, storage_keys)


async def _delete_storage_objects(storage_keys: list[str]) -> None:
    from src.infrastructure.content.s3_storage import S3ContentStorage

    storage = S3ContentStorage(
        bucket=config.S3_BUCKET_NAME,
        region=config.AWS_REGION,
        access_key=config.AWS_ACCESS_KEY_ID,
        secret_key=config.AWS_SECRET_ACCESS_KEY,
    )
    for storage_key in storage_keys:
        try:
            await storage.delete(storage_key)
        except Exception:
            logger.exception("Failed to delete withdrawn user's stored content")


__all__ = ["router"]
