# 개념 정리 — `app/main.py`, `app/routers/{health,ingest,chat}.py`

## `main.py` — lifespan으로 시작/종료 훅 묶기

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kafka_task
    await ensure_collections()
    if settings.kafka_enabled:
        _kafka_task = asyncio.create_task(run_consumer())
    yield
    if _kafka_task:
        _kafka_task.cancel()
```

FastAPI의 구식 `@app.on_event("startup")` 대신 `lifespan` 컨텍스트 매니저를 쓴 이유는 이게 최신 권장 방식이기도 하고, `yield` 전/후로 시작/종료 로직을 한 함수 안에 같이 두면 "이 리소스가 언제 시작해서 언제 정리되는지"가 한눈에 보이기 때문이다. `yield` 이전(`ensure_collections`, 컨슈머 태스크 생성)이 startup, 이후(`_kafka_task.cancel()`)가 shutdown.

`_kafka_task.cancel()`만 하고 `await`로 태스크가 실제로 끝나길 기다리지 않는다 — `aiokafka`의 `consumer.stop()`이 `finally` 블록에서 호출되긴 하지만, cancel 직후 프로세스가 바로 종료되는 상황(로컬 개발 중 Ctrl+C)에서는 정리가 끝까지 실행되지 않을 수 있다. 프로덕션에서 이게 문제가 되면 `await asyncio.gather(_kafka_task, return_exceptions=True)`로 바꿔서 정리가 끝나길 기다리게 하는 게 더 안전하다 — 지금은 로컬/포트폴리오 규모라 이 정도로 남겨뒀다.

`KAFKA_ENABLED` 플래그를 `.env`로 뺀 이유는 실제로 이번 스모크 테스트에서 그대로 활용했다 — 로컬 Kafka 브로커 없이 나머지 기능(`/health`, `/docs`, `/chat`, `/ingest/doc`의 degradation 경로)만 확인하고 싶을 때 `KAFKA_ENABLED=false`로 컨슈머 태스크 자체를 안 띄우게 할 수 있다.

## `routers/health.py`

```python
@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        ai_enabled=settings.ai_enabled,
        qdrant_connected=await is_connected(),
        kafka_consumer_running=kafka_consumer.is_running,
    )
```

세 가지 하위 시스템(Gemini 키, Qdrant, Kafka)의 상태를 각각 노출한다. `qdrant_connected`는 매 호출마다 실제로 Qdrant에 `get_collections()`를 날려서 확인하는 **라이브 체크**이고, `kafka_consumer_running`은 `consumer.py`의 전역 플래그를 읽는 **캐시된 상태**다 — 이 비대칭은 의도한 것: Qdrant는 매번 물어봐도 비용이 작은 반면(로컬 네트워크 HTTP 호출 한 번), Kafka 컨슈머가 "진짜 살아있는지"를 매번 확인하려면 별도의 핑 메커니즘이 필요해서 지금은 "루프에 진입했는가"만 본다 — 브로커와의 연결이 중간에 끊겼는데 태스크 자체는 안 죽은 경우까지는 잡아내지 못하는 한계가 있다.

## `routers/ingest.py`

```python
if filename.lower().endswith(".pdf"):
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
else:
    text = raw.decode("utf-8", errors="ignore")
```

파일 확장자만 보고 PDF 여부를 판단하는 단순한 분기다. `errors="ignore"`로 디코딩 에러를 조용히 무시하게 한 건, 업로드되는 문서가 항상 UTF-8이라는 보장이 없는 상황에서 "일부 글자가 깨지더라도 문서 적재 자체는 실패하지 않게" 하기 위한 실용적 선택이다 — 다만 이건 데이터 품질보다 가용성을 우선한 트레이드오프라, 실제로 한글 문서에 EUC-KR 등 다른 인코딩이 섞여 들어오면 조용히 깨진 텍스트가 임베딩될 수 있다는 점은 알아둬야 한다.

## `routers/chat.py` — SSE 스트리밍을 스레드와 큐로 다리 놓기

가장 신경 쓴 부분. Gemini SDK의 스트리밍 생성(`generate_stream`)은 **동기 제너레이터**다. 이걸 async 핸들러 안에서 그냥 `for token in generate_stream(prompt): yield ...`처럼 돌리면, SDK가 네트워크 응답을 기다리는 동안 이벤트 루프 전체가 블로킹되어 다른 동시 요청(다른 사용자의 `/chat`, `/health` 등)이 멈춘다.

```python
queue: asyncio.Queue[str | None] = asyncio.Queue()
loop = asyncio.get_running_loop()

def produce() -> None:
    try:
        for token in generate_stream(prompt):
            loop.call_soon_threadsafe(queue.put_nowait, token)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)

loop.run_in_executor(None, produce)

while True:
    token = await queue.get()
    if token is None:
        break
    yield f"data: {json.dumps({'token': token})}\n\n"
```

동기 제너레이터(`generate_stream`)를 **별도 스레드**(`run_in_executor`)에서 통째로 소비시키고, 스레드가 받은 토큰을 `asyncio.Queue`에 넣어서 메인 이벤트 루프로 넘긴다. `queue.put_nowait`을 직접 호출하지 않고 `loop.call_soon_threadsafe`로 감싼 이유는, `asyncio.Queue`가 스레드 안전하지 않기 때문이다 — 다른 스레드에서 큐를 직접 건드리면 이벤트 루프의 내부 상태와 경쟁 상태(race condition)가 생길 수 있어서, "이 콜백을 이벤트 루프 스레드에서 실행해달라"고 위임하는 게 `call_soon_threadsafe`의 역할이다. 끝을 알리는 신호로 `None`을 큐에 넣는 건 파이썬에서 흔히 쓰는 센티널(sentinel) 패턴.

```python
yield f"event: route\ndata: {json.dumps({'route': route})}\n\n"
...
yield f"event: sources\ndata: {json.dumps(sources)}\n\n"
yield "event: done\ndata: {}\n\n"
```

SSE 스펙의 `event:` 필드로 토큰 스트림과 메타데이터(라우팅 결과, 출처, 종료 신호)를 구분했다 — 클라이언트가 이벤트 이름별로 다른 핸들러를 붙일 수 있게(예: `route` 이벤트는 UI에 "문서 검색 중" 배지를 띄우고, `sources` 이벤트는 답변 끝에 출처 목록을 붙이는 식).

## 아직 검증 안 된 부분

이 스레드+큐 브리지가 **정말로 동시 요청을 블로킹하지 않는지**는 이번 스모크 테스트에서 직접 확인하지 못했다 (Gemini 키가 없어서 실제 스트리밍 경로 자체가 `_stream_disabled()`로 빠졌음). 실제 키를 넣고 `/chat`을 두 개 이상 동시에 호출해서 서로 안 막는지 확인하는 게 다음 검증 단계로 남아있다.
