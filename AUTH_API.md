# 인증 API 연동 가이드

이 문서는 프론트엔드가 자체 회원가입·로그인과 Google 로그인을 연결할 때 사용하는 계약입니다.

## 세션 방식

- Access Token은 응답 JSON으로 전달되며 15분 동안 유효합니다.
- Refresh Token은 `HttpOnly` 쿠키로만 전달되며 기본 30일 동안 유효합니다.
- 프론트엔드는 Access Token을 메모리에 보관하고 API 요청에 `Authorization: Bearer <token>`을 추가합니다.
- Refresh Token을 사용하는 요청에는 `credentials: "include"`가 필요합니다.
- Access Token이 만료되면 `POST /auth/refresh`를 호출하고 새 Access Token으로 원래 요청을 다시 시도합니다.
- Refresh Token은 갱신할 때마다 교체되며, 로그아웃·비밀번호 재설정·탈퇴 시 취소됩니다.

## 자체 회원가입

`POST /auth/signup`

```json
{
  "email": "creator@example.com",
  "password": "15자 이상의 긴 비밀번호",
  "name": "Creator"
}
```

성공 시 `201 Created`와 로그인 응답을 반환합니다. 이미 가입된 이메일은 `409 Conflict`, 입력 형식 오류는 `422 Unprocessable Entity`입니다.

## 자체 로그인

`POST /auth/login`

```json
{
  "email": "creator@example.com",
  "password": "사용자 비밀번호"
}
```

성공 응답:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "00000000-0000-0000-0000-000000000000",
    "email": "creator@example.com",
    "name": "Creator"
  }
}
```

이메일이 없거나 비밀번호가 틀린 경우를 구분하지 않고 `401 Unauthorized`를 반환합니다.

## 세션 갱신

`POST /auth/refresh`

요청 본문은 없습니다. 브라우저가 Refresh Token 쿠키를 보내도록 설정해야 합니다.

```javascript
const response = await fetch(`${API_URL}/auth/refresh`, {
  method: "POST",
  credentials: "include",
});
```

성공 응답은 로그인 응답과 같습니다. 쿠키가 없거나 취소·만료된 경우 `401 Unauthorized`입니다.

## 로그아웃

`POST /auth/logout`

요청 본문은 없으며 `credentials: "include"`로 호출합니다. 서버 세션과 브라우저 쿠키를 함께 제거하고 항상 `204 No Content`를 반환합니다.

## Google 로그인

1. 브라우저를 `GET /auth/google/login`으로 이동시킵니다.
2. Google 인증이 끝나면 백엔드가 Refresh Token 쿠키를 설정합니다.
3. 백엔드는 `GOOGLE_AUTH_SUCCESS_URL`로 리다이렉트합니다.
4. 프론트엔드 콜백 화면은 `POST /auth/refresh`를 호출해 Access Token과 사용자 정보를 받습니다.

Access Token은 리다이렉트 URL에 포함되지 않습니다.

## 비밀번호 재설정 요청

`POST /auth/password/forgot`

```json
{
  "email": "creator@example.com"
}
```

가입 여부를 노출하지 않기 위해 존재하는 이메일과 존재하지 않는 이메일 모두 `202 Accepted`와 같은 메시지를 반환합니다. Google 로그인만 연결된 계정에는 메일을 보내지 않습니다.

AWS SES 발송 설정이 없으면 모든 요청에 `503 Service Unavailable`을 반환합니다.

## 새 비밀번호 저장

`POST /auth/password/reset`

```json
{
  "token": "이메일 링크의 token 값",
  "new_password": "15자 이상의 새로운 비밀번호"
}
```

성공 시 `204 No Content`입니다. 토큰은 기본 30분 동안 한 번만 사용할 수 있습니다. 성공하면 해당 사용자의 모든 로그인 세션이 취소됩니다.

## 내 정보와 탈퇴

- `GET /users/me`: 현재 사용자 정보
- `DELETE /users/me`: 계정 탈퇴

자체 로그인이 연결된 계정은 현재 비밀번호를 보내야 합니다.

```json
{
  "password": "현재 비밀번호"
}
```

Google 로그인만 사용하는 계정은 빈 객체 `{}`를 보냅니다. 성공 시 `204 No Content`이며 사용자 DB 데이터와 로그인 세션이 삭제되고 S3 원본 삭제가 예약됩니다.

## 인증이 필요한 일반 API

```javascript
const response = await fetch(`${API_URL}/contents`, {
  headers: { Authorization: `Bearer ${accessToken}` },
  credentials: "include",
});
```

로그아웃된 세션, 탈퇴한 사용자, 잘못되거나 만료된 Access Token은 `401 Unauthorized`입니다.

## 서버 환경변수

```dotenv
JWT_SECRET=충분히 긴 운영 비밀키
JWT_ISSUER=bbik-api
JWT_AUDIENCE=bbik-web
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30
PASSWORD_RESET_EXPIRE_MINUTES=30
FRONTEND_URL=https://main.bbik.cloud
CORS_ALLOW_ORIGINS=https://main.bbik.cloud
GOOGLE_AUTH_SUCCESS_URL=https://main.bbik.cloud/auth/callback
PASSWORD_RESET_URL=https://main.bbik.cloud/reset-password
EMAIL_FROM=no-reply@example.com
```

`EMAIL_FROM` 주소 또는 도메인은 AWS SES에서 발송 가능하도록 인증되어 있어야 합니다.
