from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    GraphState,
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

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_selector,
        {"doc_rag": "doc_rag", "pattern_search": "pattern_search"},
    )
    graph.add_edge("doc_rag", END)
    graph.add_edge("pattern_search", END)

    return graph.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def classify_and_retrieve(question: str) -> GraphState:
    result = await get_graph().ainvoke({"question": question, "sources": []})
    return result
