# 개념 정리 — `app/rag/qdrant_store.py`, `chunking.py`, `doc_store.py`, `pattern_store.py`

## `qdrant_store.py` — 공용 클라이언트와 컬렉션 초기화

```python
_client: AsyncQdrantClient | None = None

def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    return _client
```

동기 `QdrantClient`가 아니라 `AsyncQdrantClient`를 쓴 이유는 이 서비스 전체가 FastAPI(비동기) 위에서 돈다는 것과 직결된다. 동기 클라이언트를 async 핸들러 안에서 그대로 호출하면 그 요청을 처리하는 동안 이벤트 루프 전체가 블로킹된다 — `/chat`처럼 SSE로 여러 클라이언트에 동시에 스트리밍해야 하는 서비스에서는 치명적이다. `AsyncQdrantClient`는 내부적으로 `httpx.AsyncClient`를 쓰기 때문에 Qdrant와의 HTTP 왕복 동안 다른 요청을 처리할 수 있다.

```python
async def ensure_collections() -> None:
    client = get_client()
    for collection in (settings.qdrant_doc_collection, settings.qdrant_pattern_collection):
        exists = await client.collection_exists(collection)
        if not exists:
            await client.create_collection(
                collection_name=collection,
                vectors_config=qmodels.VectorParams(
                    size=settings.embedding_dimension,
                    distance=qmodels.Distance.COSINE,
                ),
            )
```

`main.py`의 `lifespan`에서 앱 시작 시 한 번 호출된다. Postgres 마이그레이션 도구 없이 컬렉션 존재 여부를 직접 체크하고 없으면 만드는 방식을 택한 건, Qdrant가 스키마리스에 가까운 벡터 DB라 별도 마이그레이션 프레임워크를 둘 만큼의 복잡도가 없기 때문 — `target-tracking-service`가 `spring.ai.vectorstore.pgvector.initialize-schema: true`로 스키마를 자동 생성하는 것과 같은 발상을 수동으로 짧게 구현한 셈이다.

**실제로 검증된 동작**: Qdrant가 떠 있지 않으면 이 함수가 예외를 던지고 `main.py`의 lifespan이 실패해서 **앱 자체가 기동하지 않는다.** 이건 의도한 설계다 — Gemini 키와 달리 Qdrant는 이 서비스의 핵심 저장소라서 "AI 기능만 꺼진 채로 서비스는 살아있는" 상태가 성립하지 않는다. Docker로 Qdrant 없이 `uvicorn app.main:app`을 실행해서 `httpx.ConnectError` → `Application startup failed. Exiting.`로 죽는 걸 직접 확인했다.

## `chunking.py` — 텍스트 분할

```python
def chunk_text(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return [c for c in splitter.split_text(text) if c.strip()]
```

직접 슬라이딩 윈도우를 구현하지 않고 `langchain-text-splitters`의 `RecursiveCharacterTextSplitter`를 그대로 가져다 쓴 이유는, 이게 이미 "문단 → 줄바꿈 → 문장 → 단어" 순으로 구분자를 시도하며 의미 단위를 최대한 안 끊는 로직을 갖고 있어서다 — 바퀴를 새로 깎을 이유가 없었다. `chunk_overlap`(기본 100자)을 둔 이유는 청크 경계에서 문맥이 잘려 검색 품질이 떨어지는 걸 막기 위함.

## `doc_store.py` — 문서 RAG용 컬렉션 (`threat_documents`)

```python
async def ingest_document(filename: str, text: str) -> tuple[str, int]:
    doc_id = str(uuid.uuid4())
    chunks = chunk_text(text)
    points = []
    for index, chunk in enumerate(chunks):
        embedding = embed_text(chunk, task_type="retrieval_document")
        points.append(qmodels.PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload={"doc_id": doc_id, "filename": filename, "chunk_index": index, "text": chunk},
        ))
    if points:
        await get_client().upsert(collection_name=settings.qdrant_doc_collection, points=points)
    return doc_id, len(points)
```

청크마다 별도 포인트 ID(`uuid4`)를 발급하면서도 `doc_id`를 payload에 같이 저장한 이유는, "이 청크가 어느 문서에서 왔는지"를 나중에 필터링/삭제할 수 있게 하기 위해서다 (지금은 필터 검색을 안 쓰지만, 문서 단위 재색인/삭제 기능을 붙일 때 이 필드가 필요해진다).

임베딩을 **청크마다 순차적으로** 호출하고 있다(`for` 루프 안에서 `embed_text` 호출) — 배치 API가 아니라서 청크가 많은 문서를 올리면 이 부분이 요청 처리 시간의 대부분을 차지한다. 지금 규모(포트폴리오용 문서 몇 개)에서는 문제가 안 되지만, 실제 대량 문서를 다룬다면 여기가 첫 번째 최적화 지점이다.

## `pattern_store.py` — 이력 탐지용 컬렉션 (`target_history`)

```python
def describe_event(event: TargetEvent) -> str:
    return (
        f"표적ID={event.targetId}, 유형={event.targetType}, "
        f"위도={event.latitude}, 경도={event.longitude}, "
        f"고도={event.altitude}m, 속도={event.speed}km/h, 상태={event.status}"
    )
```

정형 데이터(위도/경도/고도/속도 같은 숫자 필드)를 굳이 자연어 문장으로 바꿔서 임베딩하는 이유 — 벡터 임베딩 모델은 자연어 문장에서 의미적 유사도를 뽑도록 학습되어 있어서, 숫자 배열을 직접 벡터화하는 것보다 "고도 45m, 속도 280km/h로 접근"이라는 문장을 임베딩하는 쪽이 "비슷한 상황"을 더 잘 포착한다. 이건 `target-tracking-service`의 `ThreatAnalysisService`가 RAG 1단계에서 하는 것과 똑같은 아이디어를 그대로 가져온 것 — 실제로 그 서비스의 문서(`ai-analysis.md`)에 나온 "① 자연어 설명문 구성" 단계와 이 함수는 사실상 동일한 역할을 한다.

```python
async def upsert_target_event(event: TargetEvent) -> None:
    ...
    payload={
        ...
        "description": description,
        "observed_at": datetime.now(UTC).isoformat(),
    }
```

`observed_at`을 UTC로 저장한 건 이 값이 나중에 시간 범위 필터(`payload filter`)나 정렬에 쓰일 걸 감안해서다 — Kafka 이벤트 자체에는 타임스탬프 필드가 없어서(`TargetEvent.java`에 없음), 수신 시각을 대리 타임스탬프로 기록한다. 정확히는 "탐지 시각"이 아니라 "이 서비스가 색인한 시각"이라는 점은 한계로 남아있다.
