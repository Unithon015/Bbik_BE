import hmac
from secrets import token_urlsafe

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse, RedirectResponse

from src import config
from src.application.auth.dto import (
    AuthenticatedUserResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
)
from src.application.auth.service import (
    AuthenticationService,
    DuplicateEmailError,
    EmailDeliveryNotConfiguredError,
    GoogleEmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidPasswordResetTokenError,
    InvalidRefreshTokenError,
)
from src.domain.auth.entity import AuthResult
from src.infrastructure.google.client import get_login_url
from src.interface.auth.dependencies import get_auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
_OAUTH_STATE_COOKIE = "oauth_state"
_REFRESH_TOKEN_COOKIE = "refresh_token"


def _service(
    service: AuthenticationService = Depends(get_auth_service),
) -> AuthenticationService:
    # Stable override point for router tests and local adapters.
    return service


@router.post(
    "/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    service: AuthenticationService = Depends(_service),
) -> TokenResponse:
    try:
        result = await service.signup(
            email=str(body.email),
            password=body.password,
            name=body.name,
            user_agent=request.headers.get("user-agent"),
        )
    except DuplicateEmailError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _set_refresh_cookie(response, result.tokens.refresh_token)
    return _token_response(result)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthenticationService = Depends(_service),
) -> TokenResponse:
    try:
        result = await service.login_with_password(
            email=str(body.email),
            password=body.password,
            user_agent=request.headers.get("user-agent"),
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_refresh_cookie(response, result.tokens.refresh_token)
    return _token_response(result)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    service: AuthenticationService = Depends(_service),
) -> TokenResponse | Response:
    refresh_token = request.cookies.get(_REFRESH_TOKEN_COOKIE)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="유효하지 않은 로그인 세션입니다.")
    try:
        result = await service.refresh(refresh_token)
    except InvalidRefreshTokenError as exc:
        error_response = JSONResponse(status_code=401, content={"detail": str(exc)})
        _delete_refresh_cookie(error_response)
        return error_response
    _set_refresh_cookie(response, result.tokens.refresh_token)
    return _token_response(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    service: AuthenticationService = Depends(_service),
) -> None:
    await service.logout(request.cookies.get(_REFRESH_TOKEN_COOKIE))
    _delete_refresh_cookie(response)


@router.post(
    "/password/forgot",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    service: AuthenticationService = Depends(_service),
) -> MessageResponse:
    try:
        delivery = await service.create_password_reset(str(body.email))
    except EmailDeliveryNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if delivery:
        background_tasks.add_task(service.deliver_password_reset, delivery)
    return MessageResponse(
        message="가입된 이메일이라면 비밀번호 재설정 안내를 발송했습니다."
    )


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    body: ResetPasswordRequest,
    service: AuthenticationService = Depends(_service),
) -> None:
    try:
        await service.reset_password(body.token, body.new_password)
    except InvalidPasswordResetTokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/google/login")
def google_login() -> RedirectResponse:
    state_value = token_urlsafe(32)
    response = RedirectResponse(get_login_url(state_value))
    response.set_cookie(
        _OAUTH_STATE_COOKIE,
        state_value,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/google/callback")
async def google_callback(
    code: str,
    request: Request,
    state: str | None = None,
    service: AuthenticationService = Depends(_service),
) -> RedirectResponse:
    expected_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if (
        not state
        or not expected_state
        or not hmac.compare_digest(state, expected_state)
    ):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    try:
        result = await service.login(code, request.headers.get("user-agent"))
    except GoogleEmailNotVerifiedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = RedirectResponse(config.GOOGLE_AUTH_SUCCESS_URL)
    _set_refresh_cookie(response, result.tokens.refresh_token)
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    return response


def _token_response(result: AuthResult) -> TokenResponse:
    return TokenResponse(
        access_token=result.tokens.access_token,
        expires_in=result.tokens.expires_in,
        user=AuthenticatedUserResponse(
            id=result.user_id,
            email=result.email,
            name=result.name,
        ),
    )


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.set_cookie(
        _REFRESH_TOKEN_COOKIE,
        refresh_token,
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        max_age=config.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/auth",
    )


def _delete_refresh_cookie(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        _REFRESH_TOKEN_COOKIE,
        path="/auth",
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
    )


def _secure_cookie() -> bool:
    return config.ENVIRONMENT in {"production", "prod"}
