# 삐빅 백엔드

FastAPI 기반 콘텐츠 사전 모니터링 API입니다. AI가 게시 가능 여부를 판정하지 않고,
사람이 다시 확인할 후보와 근거를 Human Review Queue로 정리합니다.

## 인증 및 계정 관리

Google OAuth와 이메일·비밀번호 로그인을 지원합니다. Access Token은 짧게 유지하고,
서버에서 취소 가능한 Refresh Token 세션을 `HttpOnly` 쿠키로 제공합니다.

| Endpoint | 설명 |
| --- | --- |
| `POST /auth/signup` | 이메일·비밀번호 회원가입 |
| `POST /auth/login` | 이메일·비밀번호 로그인 |
| `POST /auth/refresh` | Access Token 갱신 및 Refresh Token 회전 |
| `POST /auth/logout` | 현재 로그인 세션 취소 |
| `POST /auth/password/forgot` | 비밀번호 재설정 이메일 요청 |
| `POST /auth/password/reset` | 일회용 토큰으로 새 비밀번호 저장 |
| `GET /auth/google/login` | Google OAuth 시작 |
| `GET /users/me` | 내 계정 정보 조회 |
| `DELETE /users/me` | 계정과 소유 콘텐츠 삭제 |

프론트엔드 요청·응답과 쿠키 계약은 [`AUTH_API.md`](AUTH_API.md)를 참고하세요.

## 콘텐츠 검수 API

`POST /contents`는 `multipart/form-data`를 받습니다. `text`, `files`는 함께 또는 각각
보낼 수 있으며, 파일은 MP4, JPG/JPEG, PNG, WEBP만 지원합니다.

```bash
curl -X POST http://localhost:8000/contents \
  -F "title=여름 캠페인 숏폼 영상" \
  -F "text=게시할 캡션 문구" \
  -F "files=@campaign.mp4;type=video/mp4"
```

응답의 `status`는 처음에 `QUEUED`입니다. 실제 분석 워커가 작업을 시작·완료하면
`ANALYZING`, `COMPLETED`, `FAILED`로 갱신합니다. `COMPLETED` 전에는 결과를 만들어
반환하지 않습니다.

| Endpoint | 설명 |
| --- | --- |
| `POST /contents` | 텍스트·이미지·영상 검수 요청 생성 |
| `GET /contents` | 최근 검수 요청 목록 |
| `GET /contents/{submission_id}` | 검수 요청과 원본 자산 메타데이터 |
| `GET /contents/{submission_id}/analysis` | 분석 상태와 검수 후보·근거 |
| `GET /contents/{submission_id}/assets/{asset_id}` | 원본 자산 조회 |

로컬/단일 EC2 MVP에서는 `UPLOAD_DIRECTORY`에 원본을 저장합니다. 다중 인스턴스나
운영 배포 전에는 같은 `ContentStorage` 포트에 S3 어댑터를 연결해야 합니다.

## 나무위키 사건 인덱스

서버가 처음 시작될 때 나무위키의 2024·2025·2026년 사건사고 분류를 자동 적재하고,
2026년 분류는 매일 자정 `Asia/Seoul`에 동기화합니다. 이미 한 번 정상 적재한 연도는
서버 재시작 때 다시 수집하지 않습니다. 수집하는 데이터는 분류 페이지의 사건 제목과 문서 URL뿐이며,
사용자에게 보여 줄 최종 근거는 뉴스·공식 출처를 별도로 연결해야 합니다.

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://..."
python scripts/sync_namu_wiki_incidents.py --years 2024 2025 2026
python scripts/seed_meta_policy_catalog.py
```

서버에 `DATABASE_URL`이 있으면 최초 적재가 시작 시 실행되고 2026년 동기화 스케줄러가
자정에 실행됩니다. `NAMU_WIKI_INITIAL_SYNC_ENABLED=false`로 최초 적재를,
`NAMU_WIKI_2026_SYNC_ENABLED=false`로 일일 동기화를 각각 비활성화할 수 있습니다. 다중 인스턴스
배포라면 최초 적재와 일일 스케줄러가 한 인스턴스에서만 실행되도록 설정하거나, AWS EventBridge에서
동일 스크립트를 하루 한 번 실행해야 합니다. RDS가 사설 서브넷이라면 초기 적재 명령도 EC2 또는 같은 VPC 내부에서 실행해야 합니다.

| Endpoint | 설명 |
| --- | --- |
| `GET /namu-wiki/incidents/{year}` | DB에 적재된 해당 연도의 활성 사건사고 인덱스 반환 |
| `POST /namu-wiki/incidents/{year}/sync` | 해당 연도(2024·2025·2026) 분류를 나무위키에서 즉시 수집·upsert 후 반환 |

일반 화면은 `GET`으로 DB 인덱스만 조회합니다. `POST .../sync`는 크롤링을 발생시키므로 운영 환경에서는 관리자 전용으로 제한해야 합니다.

사건사고 인덱스의 핵심 필드는 `id`, `title`, `year`, `risk_categories`,
`match_keywords`, `source_url`, `source_type`입니다. 나무위키 수집 시
`risk_categories`는 근거 없는 분류를 피하기 위해 빈 배열로 저장하고,
`match_keywords`는 제목·제목 토큰·정규화 제목으로 생성합니다.

## Meta 정책 카탈로그

`scripts/seed_meta_policy_catalog.py`는 Meta 커뮤니티 기준 27개 항목의 제목·공식 URL·삐빅
검수 카테고리를 `policy_catalog_entries`에 upsert합니다. 정책 본문 전문을 복제하지 않습니다.
