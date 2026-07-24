"""Thin wrapper around google-generativeai.

Mirrors the graceful-degradation behavior of the sibling Java service
(target-tracking-service): when GEMINI_API_KEY is unset, RAG/LLM callers
must fall back to rule-based behavior instead of crashing.
"""

from collections.abc import Generator

import google.generativeai as genai

from app.config import settings

if settings.ai_enabled:
    genai.configure(api_key=settings.gemini_api_key)

_chat_model = None


def _get_chat_model() -> genai.GenerativeModel:
    global _chat_model
    if _chat_model is None:
        _chat_model = genai.GenerativeModel(settings.gemini_chat_model)
    return _chat_model


def embed_text(text: str, task_type: str = "retrieval_document") -> list[float]:
    if not settings.ai_enabled:
        raise RuntimeError("GEMINI_API_KEY not configured; embeddings unavailable")

    result = genai.embed_content(
        model=settings.gemini_embedding_model,
        content=text,
        task_type=task_type,
        output_dimensionality=settings.embedding_dimension,
    )
    return result["embedding"]


def classify_question(question: str) -> str:
    """Route a natural-language question to a retrieval strategy.

    Falls back to a keyword heuristic when the LLM is unavailable or the
    response can't be parsed, so the /chat endpoint keeps working without
    an API key (same degradation contract as the Java service).
    """
    if settings.ai_enabled:
        try:
            prompt = (
                "다음 질문을 아래 두 카테고리 중 하나로만 분류해서 "
                "카테고리 이름 한 단어만 출력해줘.\n"
                "- doc_rag: 교리, 절차, 위협 인텔리전스 보고서/문서 내용에 대한 질의\n"
                "- pattern_search: 과거에 탐지된 특정 표적/이력/유사 패턴에 대한 질의\n\n"
                f"질문: {question}\n출력:"
            )
            response = _get_chat_model().generate_content(prompt)
            answer = response.text.strip().lower()
            if "pattern" in answer:
                return "pattern_search"
            if "doc" in answer:
                return "doc_rag"
        except Exception:
            pass

    keywords = ("이력", "과거", "탐지된", "유사한 표적", "패턴이 있었")
    if any(k in question for k in keywords):
        return "pattern_search"
    return "doc_rag"


def generate_stream(prompt: str) -> Generator[str, None, None]:
    if not settings.ai_enabled:
        yield "AI 비활성화 - GEMINI_API_KEY를 설정한 뒤 재시작하세요."
        return

    response = _get_chat_model().generate_content(prompt, stream=True)
    for chunk in response:
        if chunk.text:
            yield chunk.text
