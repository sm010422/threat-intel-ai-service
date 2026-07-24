# 개념 정리 — `app/kafka/consumer.py`

## 왜 `target-tracking-service`와 같은 토픽을, 다른 컨슈머 그룹으로 구독하나

```python
consumer = AIOKafkaConsumer(
    settings.kafka_topic,              # "target-tracking"
    bootstrap_servers=settings.kafka_bootstrap_servers,
    group_id=settings.kafka_group_id,  # "threat-intel-ai-service"
    auto_offset_reset="earliest",
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
)
```

Kafka의 컨슈머 그룹은 "같은 그룹 안에서는 파티션을 나눠 가지지만, 그룹이 다르면 각자 토픽 전체를 처음부터 받는다"는 게 핵심 동작이다. `target-tracking-service`(Java)는 이미 `target-tracking-group`이라는 그룹으로 이 토픽을 구독해서 WebSocket 전송 + pgvector 위협 분석에 쓰고 있다. 이 서비스는 그 그룹 이름과 겹치지 않는 `threat-intel-ai-service`라는 **새 컨슈머 그룹**을 쓰는데, 이렇게 하면:

- Java 쪽 오프셋 커밋과 완전히 독립적으로 동작한다 — 이 서비스가 죽었다 살아나도 Java 서비스의 소비 위치에 영향을 주지 않는다.
- `auto_offset_reset="earliest"`이므로, 이 서비스를 처음 배포하는 시점에 토픽에 이미 쌓여 있던 과거 이벤트까지 전부 받아서 `target_history` 컬렉션을 채운다 — "새로 배포한 순간부터의 이벤트만 쌓이는" 문제를 피한다.

## 왜 `aiokafka`를 골랐나 (Spring Kafka와의 차이)

Java 쪽(`TargetConsumer`, `TargetProducer`)은 Spring Kafka의 `KafkaTemplate`/`@KafkaListener`를 쓰는데, 이건 내부적으로 스레드 기반이다. Python 생태계에서 가장 널리 쓰이는 `kafka-python`은 동기(블로킹) 클라이언트라 FastAPI의 이벤트 루프 안에서 백그라운드 태스크로 돌리면 루프를 막아버린다. `aiokafka`는 `asyncio` 네이티브라 `async for message in consumer:` 형태로 루프를 막지 않고 소비할 수 있어서, `main.py`의 `asyncio.create_task(run_consumer())`로 다른 요청 처리와 나란히 돌아갈 수 있다.

## 메시지 하나 실패해도 컨슈머 전체가 안 죽는 이유

```python
async for message in consumer:
    try:
        event = TargetEvent.model_validate(message.value)
        await upsert_target_event(event)
        logger.info("Indexed target event: %s", event.targetId)
    except Exception:
        logger.exception("Failed to process Kafka message: %s", message.value)
```

`try/except`를 **루프 바깥이 아니라 메시지 하나 처리하는 단위 안쪽**에 둔 게 핵심이다. 바깥에 뒀다면 예외 하나(예: Gemini 키 미설정으로 `embed_text`가 던지는 `RuntimeError`, 혹은 스키마가 안 맞는 메시지)가 `async for` 루프 자체를 끝내버려서 이후 메시지를 전혀 못 받는다. 지금 구조에서는 한 메시지가 실패해도 로그만 남기고 다음 메시지로 넘어간다 — Gemini 키 없이 이 서비스를 띄워도 컨슈머 자체는 계속 살아서 `is_running = True` 상태를 유지하는 걸 실제로 확인했다 (다만 색인은 전혀 안 됨).

## `is_running` 전역 플래그

```python
is_running = False

async def run_consumer() -> None:
    global is_running
    ...
    await consumer.start()
    is_running = True
    ...
    try:
        async for message in consumer:
            ...
    finally:
        is_running = False
        await consumer.stop()
```

`GET /health`가 "지금 Kafka consumer가 정상적으로 돌고 있는지"를 보여주기 위한 상태값이다. 모듈 전역 변수로 뒀는데, 이 서비스가 프로세스당 컨슈머 인스턴스를 하나만 두는 걸 전제로 하기 때문에 가능한 단순화다 — 만약 나중에 컨슈머를 여러 개(예: 파티션별로) 두게 되면 이 플래그는 클래스 인스턴스 상태나 딕셔너리로 바꿔야 한다.

## 로컬에서는 아직 실측하지 못한 부분

이번 스모크 테스트는 `KAFKA_ENABLED=false`로 컨슈머를 꺼둔 채 진행했다 (로컬 Kafka 브로커를 안 띄워서). 그래서 `run_consumer()`가 실제 브로커에 붙어서 메시지를 받아 `target_history`에 색인하는 전체 경로는 아직 end-to-end로 확인하지 않았다 — `target-tracking-service`의 docker-compose로 Kafka를 띄운 상태에서 이 서비스를 같은 브로커에 붙여보는 게 다음 검증 단계다.
