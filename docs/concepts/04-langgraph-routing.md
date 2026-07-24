# 개념 정리 — `app/graph/nodes.py`, `app/graph/router_graph.py`

## 왜 `if/else`로 안 짜고 LangGraph를 썼나

`/chat`이 하는 일 자체는 사실 이렇게 써도 된다:

```python
route = classify_question(question)
if route == "pattern_search":
    hits = await search_patterns(question)
else:
    hits = await search_documents(question)
```

지금 시점에는 이 `if/else`와 LangGraph 버전이 기능적으로 동일하다. 그럼에도 그래프로 짠 이유는 두 가지다.

1. **노드를 늘리는 게 쉬워지는 구조를 미리 잡아두고 싶었다.** 예를 들어 "규칙 기반 위협 등급도 같이 조회하는 노드", "분류 실패 시 두 컬렉션을 모두 검색하는 노드" 등을 추가할 때, `StateGraph`에 노드 하나 얹고 엣지만 연결하면 되도록. `if/else` 체인이었다면 분기가 늘어날수록 함수 하나가 계속 커진다.
2. **원본 Notion 리서치 내용대로 "LangGraph 기반으로 질문을 분류한 뒤 라우팅"하는 패턴을 실제로 손으로 짜보는 것 자체가 이 프로젝트의 목적 중 하나였다** — 실무에서 실제로 쓰이는 아키텍처 패턴이라고 확인된 것을, 최소 기능으로라도 직접 구현해보는 것.

다만 지금 그래프가 딱 2단계(분류 → 검색)뿐이라 "이 정도면 LangGraph 없이도 됐다"는 지적은 타당하다. 이건 향후 노드가 늘어날 걸 전제로 한 선제적 구조이지, 지금 이 순간의 복잡도만 보면 다소 과한 추상화라는 점을 인지하고 있다.

## `nodes.py` — 상태와 노드

```python
class GraphState(TypedDict):
    question: str
    route: Literal["doc_rag", "pattern_search"]
    context: str
    sources: list[dict]
```

LangGraph의 `StateGraph`는 각 노드가 상태의 일부만 채워서 리턴해도 되고, 프레임워크가 이전 상태와 머지해준다. 그래서 `classify_node`는 `route`만, `doc_rag_node`/`pattern_search_node`는 `context`/`sources`만 채워서 리턴한다.

```python
def classify_node(state: GraphState) -> GraphState:
    route = classify_question(state["question"])
    return {**state, "route": route}
```

여기서 `{**state, "route": route}`처럼 **전체 상태를 복사해서 리턴**하는 방식을 택했다 — LangGraph는 부분 딕셔너리(`{"route": route}`)만 리턴해도 병합해주지만, 매번 원본 dict 전체를 스프레드해서 리턴하도록 통일한 건 "이 노드가 정확히 어떤 상태를 보고 어떤 상태를 만들어내는지"를 코드만 보고 파악하기 쉽게 하려는 선택이다.

`doc_rag_node`/`pattern_search_node`는 비동기 함수인데 `classify_node`는 동기 함수다 — LangGraph는 그래프 안에 동기/비동기 노드가 섞여 있어도 `ainvoke()`로 실행하면 알아서 처리해준다. `classify_question`이 내부적으로 동기 SDK 호출(`genai.GenerativeModel.generate_content`)이라 굳이 async로 감쌀 이유가 없어서 그대로 뒀다.

## `router_graph.py` — 그래프 조립과 조건부 엣지

```python
graph.add_edge(START, "classify")
graph.add_conditional_edges(
    "classify",
    route_selector,
    {"doc_rag": "doc_rag", "pattern_search": "pattern_search"},
)
graph.add_edge("doc_rag", END)
graph.add_edge("pattern_search", END)
```

`add_conditional_edges`의 두 번째 인자(`route_selector`)는 `state["route"]` 문자열을 그대로 리턴하는 함수고, 세 번째 인자(딕셔너리)는 그 문자열을 실제 다음 노드 이름에 매핑한다. 여기서는 두 값이 우연히 똑같아서(`"doc_rag"` → `"doc_rag"`) 매핑이 항등함수처럼 보이지만, `route_selector`가 리턴하는 값과 노드 이름이 반드시 같은 문자열일 필요는 없다는 걸 명시하려고 딕셔너리를 생략하지 않았다 — 나중에 노드 이름을 바꿔도 라우팅 로직(`classify_question`)은 안 건드려도 되게.

```python
_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
```

`StateGraph.compile()`은 매번 호출할 필요 없는 무거운(상대적으로) 작업이라 앱 전체에서 한 번만 컴파일해서 재사용한다 — `gemini_client.py`의 `_chat_model` 캐싱과 같은 지연 싱글턴 패턴.

## 왜 최종 답변 생성(LLM 스트리밍)은 그래프 밖에 있나

`doc_rag_node`/`pattern_search_node`는 컨텍스트만 채우고 끝난다 (`END`로 바로 간다). 실제 Gemini 스트리밍 생성은 `router_graph.classify_and_retrieve()`가 리턴한 결과를 받아서 `routers/chat.py`가 별도로 수행한다.

LangGraph에서 노드 하나가 토큰 스트림을 만들어내게 하려면 `astream_events`나 커스텀 스트리밍 콜백을 그래프 레벨에서 다뤄야 하는데, 이걸 FastAPI의 SSE `StreamingResponse`와 다시 엮으려면 두 겹의 스트리밍 추상화(LangGraph 스트림 → SSE 스트림)를 맞춰야 한다. 지금 규모에서는 "그래프는 검색/라우팅까지만 책임지고, 생성 스트리밍은 라우터가 직접 처리"하는 쪽이 훨씬 단순해서 이 경계를 그었다 — `docs/architecture.md`에도 같은 이유를 적어뒀다.
