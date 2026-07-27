from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    GraphState,
    assess_threat_node,
    classify_node,
    doc_rag_node,
    pattern_search_node,
    route_selector,
)

_graph = None


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("classify", classify_node)
    graph.add_node("doc_rag", doc_rag_node)
    graph.add_node("pattern_search", pattern_search_node)
    graph.add_node("assess_threat", assess_threat_node)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_selector,
        {"doc_rag": "doc_rag", "pattern_search": "pattern_search"},
    )
    graph.add_edge("doc_rag", END)
    # pattern_search 결과에만 tool-calling을 붙인다 -- 문서 RAG 질의에는
    # "표적 위협 등급"이라는 개념 자체가 적용되지 않는다.
    graph.add_edge("pattern_search", "assess_threat")
    graph.add_edge("assess_threat", END)

    return graph.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def classify_and_retrieve(question: str) -> GraphState:
    result = await get_graph().ainvoke({"question": question, "sources": [], "tool_call": None})
    return result
