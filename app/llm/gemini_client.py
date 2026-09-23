"""Thin wrapper around google-generativeai.

Mirrors the graceful-degradation behavior of the sibling Java service
(target-tracking-service): when GEMINI_API_KEY is unset, RAG/LLM callers
must fall back to rule-based behavior instead of crashing.
"""

import asyncio
import inspect
from collections.abc import Generator

import google.generativeai as genai

from app.config import settings
from app.graph.tools import ALL_TOOLS, TOOL_FUNCTIONS

# 요청 하나당 이 이상 도구 호출 스텝을 밟지 않는다. Gemini 무료 tier 일일 한도를
# 이미 여러 번 실측으로 소진해본 프로젝트라(docs/concepts/11-tool-calling-node-and-ragas-evaluation.md,
# target-tracking-service의 ANALYSIS_COOLDOWN 등), 모델이 도구를 계속 연쇄 호출하며
# 루프를 도는 최악의 경우에도 요청 하나가 쓰는 LLM 호출 수를 못박아둔다.
MAX_TOOL_STEPS = 3

if settings.ai_enabled:
    genai.configure(api_key=settings.gemini_api_key)

_chat_model = None
_tool_model = None


def _get_chat_model() -> genai.GenerativeModel:
    global _chat_model
    if _chat_model is None:
        _chat_model = genai.GenerativeModel(settings.gemini_chat_model)
    return _chat_model


def _get_tool_model() -> genai.GenerativeModel:
    global _tool_model
    if _tool_model is None:
        _tool_model = genai.GenerativeModel(settings.gemini_chat_model, tools=[ALL_TOOLS])
    return _tool_model


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


async def call_with_tools(prompt: str) -> list[dict]:
    """멀티스텝 플래닝 에이전트: 모델이 스스로 판단해 0~MAX_TOOL_STEPS개의 도구를
    순서대로 호출한다 (assess_threat_level → lookup_response_procedure →
    check_intercept_asset_availability 같은 체인도 가능). 그래프가 어떤 도구를
    부를지 강제하지 않는다 -- 매 스텝마다 "이 도구가 더 필요한가, 아니면 이제
    답할 수 있는가"를 Gemini의 function-calling 응답(function_call vs 순수 텍스트)
    으로 판단한다.

    ChatSession을 써서 이전 스텝의 함수 호출/결과가 대화 히스토리에 자동으로
    쌓이게 한다 -- 그래야 두 번째 도구를 고를 때 첫 번째 도구 결과를 모델이
    참고할 수 있다.
    """
    if not settings.ai_enabled:
        return []

    calls: list[dict] = []
    chat = _get_tool_model().start_chat()
    message: str | genai.protos.Content = prompt

    for _ in range(MAX_TOOL_STEPS):
        try:
            response = await asyncio.to_thread(chat.send_message, message)
            part = response.candidates[0].content.parts[0]
        except Exception:
            break

        function_call = getattr(part, "function_call", None)
        if not function_call or function_call.name not in TOOL_FUNCTIONS:
            break  # 더 이상 도구가 필요 없다고 모델이 판단 -- 순수 텍스트 응답을 반환함

        fn = TOOL_FUNCTIONS[function_call.name]
        args = dict(function_call.args)
        try:
            result = await fn(**args) if inspect.iscoroutinefunction(fn) else fn(**args)
        except Exception as e:
            result = f"도구 실행 실패: {e}"

        calls.append({"tool_called": True, "tool_name": function_call.name, "tool_result": str(result)})

        # 함수 실행 결과를 다음 턴에 넘겨서, 모델이 이 결과를 바탕으로 다음 도구를
        # 고를지 여기서 멈출지 스스로 판단하게 한다.
        message = genai.protos.Content(
            parts=[
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=function_call.name,
                        response={"result": str(result)},
                    )
                )
            ]
        )

    return calls


def generate_stream(prompt: str) -> Generator[str, None, None]:
    if not settings.ai_enabled:
        yield "AI 비활성화 - GEMINI_API_KEY를 설정한 뒤 재시작하세요."
        return

    response = _get_chat_model().generate_content(prompt, stream=True)
    for chunk in response:
        if chunk.text:
            yield chunk.text
