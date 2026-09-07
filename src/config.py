import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/baekend")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
DEFAULT_JWT_SECRET = "change-me-in-production"
JWT_SECRET = os.getenv("JWT_SECRET", DEFAULT_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_ISSUER = os.getenv("JWT_ISSUER", "bbik-api")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "bbik-web")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
PASSWORD_RESET_EXPIRE_MINUTES = int(os.getenv("PASSWORD_RESET_EXPIRE_MINUTES", "30"))
# Backwards-compatible alias for code outside the authentication session flow.
JWT_EXPIRE_MINUTES = ACCESS_TOKEN_EXPIRE_MINUTES
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
GOOGLE_AUTH_SUCCESS_URL = os.getenv(
    "GOOGLE_AUTH_SUCCESS_URL", f"{FRONTEND_URL.rstrip('/')}/auth/callback"
)
PASSWORD_RESET_URL = os.getenv(
    "PASSWORD_RESET_URL", f"{FRONTEND_URL.rstrip('/')}/reset-password"
)
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
UPLOAD_DIRECTORY = os.getenv("UPLOAD_DIRECTORY", "/tmp/bbibik-uploads")
OPEN_API_KEY = os.getenv("OPEN_API_KEY", "")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")
MAX_UPLOAD_FILE_BYTES = 30 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_FILES = 5
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]


def validate_security_configuration() -> None:
    if ENVIRONMENT in {"production", "prod"} and JWT_SECRET == DEFAULT_JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be configured in production")
    if ENVIRONMENT in {"production", "prod"} and "*" in CORS_ALLOW_ORIGINS:
        raise RuntimeError("CORS_ALLOW_ORIGINS must not contain '*' in production")
