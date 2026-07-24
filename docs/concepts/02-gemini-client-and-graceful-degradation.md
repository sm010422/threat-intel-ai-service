# 개념 정리 — `app/llm/gemini_client.py`

## 이 파일이 하는 일

`google-generativeai` SDK를 감싸는 얇은 래퍼. 세 가지 함수만 외부에 노출한다.

- `embed_text(text, task_type)` — 문서/쿼리 임베딩
- `classify_question(question)` — `/chat` 질문을 `doc_rag` / `pattern_search`로 분류
- `generate_stream(prompt)` — 최종 답변을 토큰 단위로 스트리밍 생성

## Graceful Degradation을 어디까지 흉내냈나

`target-tracking-service`의 `ThreatAnalysisService`는 `GEMINI_API_KEY`가 없으면 규칙 기반 위협 등급으로 자동 폴백한다. 이 서비스도 같은 원칙을 세 함수 각각에 다르게 적용했다.

```python
def embed_text(text: str, task_type: str = "retrieval_document") -> list[float]:
    if not settings.ai_enabled:
        raise RuntimeError("GEMINI_API_KEY not configured; embeddings unavailable")
    ...
```

임베딩은 **폴백이 불가능한 영역**이라 그냥 예외를 던진다 — "규칙 기반 임베딩" 같은 건 존재하지 않으니, 여기는 상위 호출자(`doc_store.py`, `pattern_store.py`, Kafka consumer)가 이 예외를 잡아서 처리하도록 책임을 위로 넘긴다. 실제로 Kafka consumer(`app/kafka/consumer.py`)는 메시지 하나 처리 실패를 로그만 남기고 다음 메시지로 넘어가게 짜여 있어서, 키가 없어도 컨슈머 자체는 안 죽고 계속 돈다 — 그냥 아무것도 색인되지 않을 뿐.

```python
def classify_question(question: str) -> str:
    if settings.ai_enabled:
        try:
            ...
            return "pattern_search" or "doc_rag" (파싱 성공 시)
        except Exception:
            pass

    keywords = ("이력", "과거", "탐지된", "유사한 표적", "패턴이 있었")
    if any(k in question for k in keywords):
        return "pattern_search"
    return "doc_rag"
```

분류는 **키워드 휴리스틱으로 완전히 대체 가능**하기 때문에, LLM 호출이 실패하거나(네트워크 문제, rate limit) 키가 아예 없으면 조용히 휴리스틱으로 떨어진다. `except Exception: pass`로 모든 예외를 삼키는 게 다소 거친 처리이긴 한데, "분류를 못 하면 서비스 전체가 죽는 것"보다는 "차선책으로라도 라우팅해서 응답은 나가게 하는 것"이 이 기능의 우선순위라 의도적으로 넓게 잡았다.

```python
def generate_stream(prompt: str) -> Generator[str, None, None]:
    if not settings.ai_enabled:
        yield "AI 비활성화 - GEMINI_API_KEY를 설정한 뒤 재시작하세요."
        return
    ...
```

이건 사실 `chat.py` 라우터 레벨에서 이미 `settings.ai_enabled`를 먼저 체크해서 이 분기까지 도달하지 않도록 막아뒀다 (`routers/chat.py`의 `_stream_disabled()` 참고). 그래도 `generate_stream`을 다른 경로에서 직접 호출할 가능성을 생각해서 함수 자체에도 안전장치를 남겨뒀다 — 방어적 코드가 두 겹인 셈인데, 이 경우엔 "이 함수를 호출하는 진입점이 늘어나도 항상 안전하다"는 계약을 지키는 쪽을 택했다.

## 임베딩 함수를 `task_type`으로 분리한 이유

```python
embed_text(chunk, task_type="retrieval_document")   # 저장할 때
embed_text(query, task_type="retrieval_query")       # 검색할 때
```

Gemini 임베딩 API는 같은 텍스트라도 "이게 나중에 검색될 문서인지" vs "지금 검색하는 질의인지"를 알려주면 임베딩 품질이 달라진다(비대칭 임베딩). `doc_store.py`/`pattern_store.py`에서 저장 시점과 검색 시점에 각각 다른 `task_type`을 넘기는 걸 강제하기 위해, 이 값을 함수 시그니처에 노출된 필수 개념으로 남겨뒀다 — 기본값을 `retrieval_document`로 잡아둔 건 "적재" 경로가 더 자주 호출되는 쪽이라서다.

## 모델 인스턴스를 모듈 전역에 캐싱한 이유

```python
_chat_model = None

def _get_chat_model() -> genai.GenerativeModel:
    global _chat_model
    if _chat_model is None:
        _chat_model = genai.GenerativeModel(settings.gemini_chat_model)
    return _chat_model
```

`GenerativeModel` 객체 생성 자체는 가벼운 편이지만, 매 요청마다 새로 만들 이유가 없어서 지연 초기화(lazy singleton) 패턴을 썼다. `genai.configure(api_key=...)`는 모듈 임포트 시점에 한 번만 호출되는데(`if settings.ai_enabled: genai.configure(...)`), 이건 `.env`를 나중에 바꾸고 프로세스를 재시작하지 않으면 반영되지 않는다는 뜻이다 — Java 서비스가 Spring 설정을 부팅 시 한 번 바인딩하는 것과 동일한 제약이고, 실제로 README에 "키를 넣은 뒤 재시작 필요"라고 명시한 이유이기도 하다.
