import base64
from dataclasses import dataclass, replace
import json

from openai import AsyncOpenAI

from src.application.content.review_context import ReviewContext
from src.domain.audience_profile.entity import AudienceProfile
from src.domain.content.entity import EvidenceLayer, FindingEvidence, ReviewFinding, ReviewPriority
from src.infrastructure.policy_catalog.context import IncidentPromptContext, PolicyPromptContext


_COMMON_PROMPT = """당신은 한국 콘텐츠 크리에이터를 위한 콘텐츠 사전 검수 보조 AI입니다.

입력된 텍스트, 이미지, 또는 둘 다를 분석하여 사람이 검토해야 할 항목만 추출하세요.

중요 원칙:
- 게시 가능/불가, 정책 위반, 불법 여부를 단정하지 마세요.
- 숫자 위험 점수를 부여하지 마세요.
- 인용·보도·비판·교육 맥락을 고려하세요.
- 실제 검토가 필요한 항목만 후보화하고, 문제가 없으면 findings를 빈 배열로 반환하세요.
- 텍스트와 이미지가 함께 제공된 경우, 둘의 맥락이 일치하는지 교차 검토하세요. 텍스트만 보면 무해하더라도 이미지가 다른 맥락을 나타낼 수 있고, 그 반대도 마찬가지입니다.

Risk 분류 (category_code):
R-01: 정치·선거 맥락
R-02: 역사·국가·기념일 맥락
R-03: 혐오·차별·젠더·문화
R-04: 민감 발언·욕설·성적·공격 표현
R-05: 평판·사실관계 확인 필요 주장
R-06: 사건·재난·애도 시의성
R-07: 제3자 개인정보·노출
R-08: 광고·협찬 컴플라이언스

우선순위:
HIGH: 즉각 검토 필요
MEDIUM: 검토 권장
LOW: 참고 사항

type 필드 규칙:
- 각 finding은 반드시 하나의 소스에만 귀속됩니다.
- 텍스트에서 발견: ["text"]
- 이미지에서 발견: ["image"]
- 영상에서 발견: ["video"]
- 텍스트와 이미지 모두에서 동일한 문제가 발견되면 각각 별도의 finding으로 분리하세요.
- ["text", "image"] 처럼 두 소스를 하나의 finding에 묶지 마세요."""


_GENERAL_SYSTEM_PROMPT = f"""{_COMMON_PROMPT}

이 단계는 외부 정책·사건 DB를 사용하지 않는 일반 검수입니다.
일반 검수 후보에는 확인되지 않은 정책 URL을 만들지 말고 evidences를 빈 배열로 두세요.

검색 보조 정보도 함께 반환하세요. search_context는 findings가 있든 없든 작성하며,
이미지 OCR 문구, 고유명사, 사건명, 인물·단체명, 민감 주제어를 중심으로 구성하세요.

다음 JSON 형식으로만 응답하세요:
{{
  "title": "콘텐츠를 한 줄로 표현한 제목 (20자 이내)",
  "findings": [
    {{
      "type": ["text"],
      "category_code": "R-03",
      "priority": "HIGH",
      "signal_type": "혐오 표현",
      "reason": "검토가 필요한 이유를 1-2문장으로 설명",
      "excerpt": "문제가 되는 원문 인용 또는 이미지 묘사",
      "evidences": []
    }}
  ],
  "search_context": {{
    "summary": "콘텐츠의 사실적 내용과 맥락을 최대 2문장으로 요약",
    "terms": ["고유명사", "사건명", "OCR 문구", "민감 주제어"]
  }}
}}"""


@dataclass(frozen=True)
class GeneralAnalysisResult:
    findings: list[ReviewFinding]
    search_summary: str
    search_terms: tuple[str, ...]
    title: str | None

    def retrieval_query(self, original_text: str | None) -> str:
        parts = [original_text or "", self.search_summary, *self.search_terms]
        return "\n".join(part for part in parts if part).strip()


async def analyze_general(
    *,
    text: str | None = None,
    images: list[tuple[bytes, str]] | None = None,
    audience_profile: AudienceProfile | None = None,
    review_context: ReviewContext | None = None,
    api_key: str,
) -> GeneralAnalysisResult:
    raw = await _request_json(
        text=text,
        images=images or [],
        api_key=api_key,
        system_prompt=_with_audience_context(
            _GENERAL_SYSTEM_PROMPT,
            audience_profile,
            review_context,
        ),
    )
    search_context = raw.get("search_context") or {}
    terms = tuple(
        str(term).strip() for term in search_context.get("terms", [])[:20]
        if str(term).strip()
    )
    raw_title = str(raw.get("title") or "").strip()[:40] or None
    return GeneralAnalysisResult(
        findings=[replace(finding, evidences=[]) for finding in _parse(raw.get("findings", []))],
        search_summary=str(search_context.get("summary", "")).strip(),
        search_terms=terms,
        title=raw_title,
    )


async def analyze_references(
    *,
    text: str | None = None,
    images: list[tuple[bytes, str]] | None = None,
    api_key: str,
    provisional_findings: list[ReviewFinding],
    policy_context: list[PolicyPromptContext],
    incident_context: list[IncidentPromptContext],
    audience_profile: AudienceProfile | None = None,
    review_context: ReviewContext | None = None,
) -> list[ReviewFinding]:
    if not policy_context and not incident_context:
        return list(provisional_findings)
    raw = await _request_json(
        text=text,
        images=images or [],
        api_key=api_key,
        system_prompt=_with_audience_context(
            _build_reference_system_prompt(
                policy_context,
                incident_context,
                provisional_findings,
            ),
            audience_profile,
            review_context,
        ),
    )
    return _merge_context_review(
        raw,
        provisional_findings,
        policy_context,
        incident_context,
    )

async def _request_json(
    *,
    text: str | None,
    images: list[tuple[bytes, str]],
    api_key: str,
    system_prompt: str,
) -> dict:
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user_content(text, images)},
        ],
        temperature=0.1,
    )
    content = response.choices[0].message.content
    return json.loads(content) if content else {}


def _build_reference_system_prompt(
    policy_context: list[PolicyPromptContext],
    incident_context: list[IncidentPromptContext],
    provisional_findings: list[ReviewFinding],
) -> str:
    rows: list[str] = []
    for policy in policy_context:
        media_types = ", ".join(policy.applicable_media_types)
        hints = " / ".join(policy.detection_hints)
        rows.append(
            f'- RULE | META_COMMUNITY_STANDARDS | "{policy.title}" | {policy.source_url}\n'
            f"  분류: {policy.review_category}; 적용: {media_types}\n"
            f"  요약: {policy.policy_summary}\n"
            f"  검수 신호: {hints}"
        )
    for incident in incident_context:
        categories = ", ".join(incident.risk_categories) or "미분류"
        rows.append(
            f'- MEMORY | {incident.source_type} | "{incident.title}" | {incident.source_url}\n'
            f"  연도: {incident.year}; 위험 분류: {categories}"
        )

    provisional_rows = [
        f'[{index}] {finding.category_code} | {finding.priority.value} | "{finding.signal_type}"\n'
        f"  이유: {finding.reason}\n"
        f"  발췌: {finding.excerpt or '없음'}"
        for index, finding in enumerate(provisional_findings)
    ]

    return f"""{_COMMON_PROMPT}

이 단계는 1차 일반 검수 후보와 DB 근거를 함께 대조하는 Context Review입니다.
아래 일반 검수 후보는 기본적으로 보존해야 합니다. 각 후보를 실제 맥락에 따라 KEEP, REVISE, DROP 중 하나로만 명시적으로 판단하세요.
- KEEP: 기존 후보를 그대로 유지합니다.
- REVISE: finding_index의 후보를 finding 객체로 교체합니다.
- DROP: DB 근거와 콘텐츠 맥락상 사람이 검토할 필요가 없을 때만 제거합니다.
- ADD: 일반 검수에 없고 아래 DB 후보와 콘텐츠가 직접 관련된 새 검수 후보만 new_findings에 추가합니다.
- 제목이나 키워드가 우연히 같다는 이유만으로 REVISE, DROP, ADD하지 마세요.
- DB 근거가 없거나 판단이 불확실하면 기존 후보는 KEEP하세요.
- evidences에는 아래 DB 후보 중 직접 관련된 근거만 최대 3개 넣으세요.
- layer, title, source_url, provider는 아래 값을 그대로 복사하세요.
- 제공되지 않은 정책, 사건, URL을 새로 만들지 마세요.

일반 검수 후보:
{chr(10).join(provisional_rows) or '(없음)'}

DB 후보:
{chr(10).join(rows)}

다음 JSON 형식으로만 응답하세요:
{{
  "reviews": [
    {{
      "finding_index": 0,
      "action": "KEEP",
      "reason": "기존 후보를 유지하는 이유"
    }},
    {{
      "finding_index": 1,
      "action": "REVISE",
      "reason": "맥락 반영 이유",
      "finding": {{
        "type": ["text"],
        "category_code": "R-04",
        "priority": "LOW",
        "signal_type": "맥락 반영 표현",
        "reason": "검토가 필요한 이유",
        "excerpt": "문제가 되는 원문 인용 또는 이미지 묘사",
        "evidences": []
      }}
    }},
    {{
      "finding_index": 2,
      "action": "DROP",
      "reason": "제거 이유"
    }}
  ],
  "new_findings": [
    {{
      "type": ["text"],
      "category_code": "R-06",
      "priority": "MEDIUM",
      "signal_type": "사건 맥락",
      "reason": "DB 근거와 대조해 검토가 필요한 이유",
      "excerpt": "문제가 되는 원문 인용 또는 이미지 묘사",
      "evidences": [
        {{
          "layer": "MEMORY",
          "title": "위 후보에 적힌 정확한 제목",
          "source_url": "위 후보에 적힌 정확한 URL",
          "provider": "NAMU_WIKI",
          "excerpt": "관련 근거 요약"
        }}
      ]
    }}
  ]
}}"""

def _audience_context_prompt(profile: AudienceProfile) -> str:
    return """Account review context (configured once by the account owner):
- Main content categories: {content_categories}
- Main viewer contexts: {audience_contexts}
- Account purposes: {account_purposes}

Use this context only to decide whether a real expression should be recommended for human review. Do not infer unprovided personal characteristics, and do not create a finding based on age or gender alone.""".format(
        content_categories=", ".join(profile.content_categories),
        audience_contexts=", ".join(profile.audience_contexts),
        account_purposes=", ".join(profile.account_purposes),
    )


def _with_audience_context(
    system_prompt: str,
    audience_profile: AudienceProfile | None,
    review_context: ReviewContext | None,
) -> str:
    additions: list[str] = []
    if audience_profile:
        additions.append(_audience_context_prompt(audience_profile))
    if review_context and review_context.focus_topics:
        additions.append(f"Priority review topics: {', '.join(review_context.focus_topics)}")
    if not additions:
        return system_prompt
    additional_context = "\n\n".join(additions)
    return f"{system_prompt}\n\n{additional_context}"


def _build_user_content(text: str | None, images: list[tuple[bytes, str]]) -> list[dict]:
    parts: list[dict] = []
    if text and images:
        parts.append({"type": "text", "text": f"다음 텍스트와 이미지를 함께 검수해주세요.\n\n텍스트:\n{text[:8000]}"})
    elif text:
        parts.append({"type": "text", "text": f"다음 텍스트를 검수해주세요:\n\n{text[:8000]}"})
    elif images:
        parts.append({"type": "text", "text": "다음 이미지를 검수해주세요:"})

    for content, mime_type in images[:4]:
        b64 = base64.b64encode(content).decode()
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{b64}", "detail": "high"},
        })
    return parts


def _merge_context_review(
    raw: dict,
    provisional_findings: list[ReviewFinding],
    policy_context: list[PolicyPromptContext],
    incident_context: list[IncidentPromptContext],
) -> list[ReviewFinding]:
    allowed = _allowed_evidence_keys(policy_context, incident_context)
    reviewed_findings: list[ReviewFinding | None] = list(provisional_findings)
    reviewed_indexes: set[int] = set()

    for review in raw.get("reviews", []):
        if not isinstance(review, dict):
            continue
        index = review.get("finding_index")
        action = str(review.get("action") or "").strip().upper()
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index >= len(reviewed_findings)
            or index in reviewed_indexes
            or action not in {"KEEP", "REVISE", "DROP"}
        ):
            continue
        if action == "REVISE":
            replacement = review.get("finding")
            if not isinstance(replacement, dict):
                continue
            parsed = _parse([replacement])
            if not parsed:
                continue
            reviewed_findings[index] = _with_whitelisted_evidence(parsed[0], allowed)
        elif action == "DROP":
            reviewed_findings[index] = None
        reviewed_indexes.add(index)

    additions = []
    for finding in _parse(raw.get("new_findings", [])):
        validated = _with_whitelisted_evidence(finding, allowed)
        if validated.evidences:
            additions.append(validated)

    return [finding for finding in reviewed_findings if finding is not None] + additions


def _allowed_evidence_keys(
    policy_context: list[PolicyPromptContext],
    incident_context: list[IncidentPromptContext],
) -> set[tuple[EvidenceLayer, str, str, str]]:
    allowed = {
        (EvidenceLayer.RULE, policy.title, policy.source_url, "META_COMMUNITY_STANDARDS")
        for policy in policy_context
    }
    allowed.update(
        (EvidenceLayer.MEMORY, incident.title, incident.source_url, incident.source_type)
        for incident in incident_context
    )
    return allowed


def _with_whitelisted_evidence(
    finding: ReviewFinding,
    allowed: set[tuple[EvidenceLayer, str, str, str]],
) -> ReviewFinding:
    evidences = [
        evidence for evidence in finding.evidences
        if (evidence.layer, evidence.title, evidence.source_url, evidence.provider) in allowed
    ][:3]
    return replace(finding, evidences=evidences)

def _parse(items: list[dict]) -> list[ReviewFinding]:
    findings = []
    for item in items:
        try:
            priority = ReviewPriority(item.get("priority", "LOW"))
        except ValueError:
            priority = ReviewPriority.LOW

        evidences = []
        for ev in item.get("evidences", [])[:3]:
            try:
                layer = EvidenceLayer(ev.get("layer", "RULE"))
            except ValueError:
                layer = EvidenceLayer.RULE
            evidences.append(FindingEvidence(
                layer=layer,
                title=ev.get("title", ""),
                source_url=ev.get("source_url", ""),
                excerpt=ev.get("excerpt"),
                provider=ev.get("provider"),
            ))

        findings.append(ReviewFinding(
            category_code=item.get("category_code", "R-00"),
            priority=priority,
            signal_type=item.get("signal_type", ""),
            reason=item.get("reason", ""),
            excerpt=item.get("excerpt"),
            evidences=evidences,
            media_types=item.get("type", []),
        ))
    return findings
