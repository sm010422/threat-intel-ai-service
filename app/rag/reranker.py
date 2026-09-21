"""Cohere Rerank를 통한 리랭킹.

app/llm/gemini_client.py와 같은 graceful-degradation 원칙: COHERE_API_KEY가
없으면 벡터 유사도 순서를 그대로 top_n으로 자르기만 하고, 리랭크 호출 자체가
실패해도(네트워크/쿼터) 같은 방식으로 폴백한다 -- 리랭커는 검색 품질을 높이는
보강 단계일 뿐, 리랭커가 죽었다고 /chat 전체가 죽어서는 안 된다.
"""

import cohere

from app.config import settings

_client: cohere.ClientV2 | None = None


def _get_client() -> cohere.ClientV2:
    global _client
    if _client is None:
        _client = cohere.ClientV2(api_key=settings.cohere_api_key)
    return _client


def rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    if not settings.rerank_enabled or len(candidates) <= 1:
        return candidates[:top_n]

    try:
        response = _get_client().rerank(
            model=settings.cohere_rerank_model,
            query=query,
            documents=[c["text"] for c in candidates],
            top_n=min(top_n, len(candidates)),
        )
    except Exception:
        return candidates[:top_n]

    return [{**candidates[result.index], "score": result.relevance_score} for result in response.results]
