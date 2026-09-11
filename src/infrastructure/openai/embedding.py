from openai import AsyncOpenAI

INCIDENT_EMBEDDING_MODEL = "text-embedding-3-small"
INCIDENT_EMBEDDING_DIMENSIONS = 1536
INCIDENT_QUERY_FALLBACK_LENGTH = 1000


def build_incident_embedding_text(
    title: str,
    match_keywords: list[str] | tuple[str, ...],
    risk_categories: list[str] | tuple[str, ...],
) -> str:
    rows = []
    if title.strip():
        rows.append(f"제목: {title.strip()}")
    keywords = [item.strip() for item in match_keywords if item.strip()]
    if keywords:
        rows.append(f"키워드: {', '.join(keywords)}")
    categories = [item.strip() for item in risk_categories if item.strip()]
    if categories:
        rows.append(f"위험 분류: {', '.join(categories)}")
    return "\n".join(rows)


def build_incident_query_text(summary: str, terms: tuple[str, ...], original_text: str | None) -> str:
    rows = []
    if summary.strip():
        rows.append(summary.strip())
    cleaned_terms = [term.strip() for term in terms if term.strip()]
    if cleaned_terms:
        rows.append(f"용어: {', '.join(cleaned_terms)}")
    if rows:
        return "\n".join(rows)
    return (original_text or "").strip()[:INCIDENT_QUERY_FALLBACK_LENGTH]


async def create_embeddings(inputs: list[str], *, api_key: str) -> list[list[float]]:
    if not inputs:
        return []
    response = await AsyncOpenAI(api_key=api_key).embeddings.create(
        model=INCIDENT_EMBEDDING_MODEL,
        input=inputs,
        dimensions=INCIDENT_EMBEDDING_DIMENSIONS,
        encoding_format="float",
    )
    return [list(item.embedding) for item in response.data]