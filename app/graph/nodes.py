from typing import Literal, TypedDict

from app.llm.gemini_client import call_with_tools, classify_question
from app.rag.doc_store import search_documents
from app.rag.pattern_store import search_patterns


class GraphState(TypedDict):
    question: str
    route: Literal["doc_rag", "pattern_search"]
    context: str
    sources: list[dict]
    tool_call: dict | None


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


def assess_threat_node(state: GraphState) -> GraphState:
    """pattern_search 결과 중 가장 유사한 표적을 골라, 위협 등급 평가가 필요한지를
    Gemini의 판단에 맡긴다 (function-calling -- 그래프가 강제로 부르는 게 아니라
    모델이 assess_threat_level 도구 호출 여부를 스스로 결정한다).
    """
    sources = state.get("sources") or []
    if not sources:
        return {**state, "tool_call": None}

    top = sources[0]["metadata"]
    prompt = (
        "다음 표적 이력을 보고, 위협 수준을 정량적으로 평가하는 게 질문에 답하는 데 "
        "도움이 된다고 판단되면 assess_threat_level 도구를 호출하라. 그렇지 않으면 "
        "아무 도구도 호출하지 말고 빈 응답을 반환하라.\n\n"
        f"표적유형={top.get('target_type')}, 고도={top.get('altitude')}m, 속도={top.get('speed')}km/h\n"
        f"사용자 질문: {state['question']}"
    )
    result = call_with_tools(prompt)
    return {**state, "tool_call": result if result.get("tool_called") else None}
