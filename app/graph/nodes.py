from typing import Literal, TypedDict

from app.llm.gemini_client import call_with_tools, classify_question
from app.rag.doc_store import search_documents
from app.rag.pattern_store import search_patterns


class GraphState(TypedDict):
    question: str
    route: Literal["doc_rag", "pattern_search"]
    context: str
    sources: list[dict]
    tool_calls: list[dict]


def classify_node(state: GraphState) -> GraphState:
    route = classify_question(state["question"])
    return {**state, "route": route}


def route_selector(state: GraphState) -> str:
    return state["route"]


async def doc_rag_node(state: GraphState) -> GraphState:
    hits = await search_documents(state["question"])
    context = "\n---\n".join(h["text"] for h in hits) or "관련 문서를 찾지 못했습니다."
    return {**state, "context": context, "sources": hits}


async def pattern_search_node(state: GraphState) -> GraphState:
    hits = await search_patterns(state["question"])
    context = "\n---\n".join(h["text"] for h in hits) or "유사한 과거 탐지 이력을 찾지 못했습니다."
    return {**state, "context": context, "sources": hits}


async def assess_threat_node(state: GraphState) -> GraphState:
    """pattern_search 결과 중 가장 유사한 표적을 근거로, 필요한 도구를 순서대로
    호출하는 멀티스텝 플래닝을 Gemini에 맡긴다 (function-calling -- 그래프가
    어떤 도구를 부를지 강제하지 않는다. 위협 등급 평가 -> 대응 절차 조회 -> 요격
    자산 확인까지 모델이 스스로 필요한 만큼만 체인으로 호출한다).
    """
    sources = state.get("sources") or []
    if not sources:
        return {**state, "tool_calls": []}

    top = sources[0]["metadata"]
    prompt = (
        "다음 표적 이력을 보고, 질문에 답하는 데 필요한 도구를 순서대로 호출하라. "
        "위협 등급을 정량 평가해야 하면 assess_threat_level, 구체적인 대응 절차가 "
        "궁금하면 lookup_response_procedure, 실제 대응 여력(요격 자산)이 궁금하면 "
        "check_intercept_asset_availability를 호출하라. 이미 답할 수 있으면 도구를 "
        "더 호출하지 말고 멈춰라.\n\n"
        f"표적유형={top.get('target_type')}, 고도={top.get('altitude')}m, 속도={top.get('speed')}km/h\n"
        f"사용자 질문: {state['question']}"
    )
    calls = await call_with_tools(prompt)
    return {**state, "tool_calls": calls}
