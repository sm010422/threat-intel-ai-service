from typing import Literal, TypedDict

from app.llm.gemini_client import classify_question
from app.rag.doc_store import search_documents
from app.rag.pattern_store import search_patterns


class GraphState(TypedDict):
    question: str
    route: Literal["doc_rag", "pattern_search"]
    context: str
    sources: list[dict]


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
