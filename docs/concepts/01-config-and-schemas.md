# 개념 정리 — `app/config.py`, `app/models/schemas.py`

## `app/config.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    ...
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "target-tracking"
    kafka_group_id: str = "threat-intel-ai-service"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)


settings = Settings()
```

`pydantic-settings`의 `BaseSettings`는 필드 이름을 대문자 스네이크케이스 환경변수로 자동 매핑한다 (`gemini_api_key` ↔ `GEMINI_API_KEY`) — Spring Boot의 relaxed binding과 사실상 동일한 아이디어를, Python 쪽에서 pydantic이 대신 해주는 셈이다. `.env` 파일도 같은 규칙으로 읽으므로, `target-tracking-service`가 쓰던 것과 동일한 `.env` 관례(`echo "GEMINI_API_KEY=..." >> .env`)를 그대로 재사용할 수 있었다.

`ai_enabled`를 필드가 아니라 **property**로 둔 이유: `gemini_api_key`가 비어있는지 여부로 매번 계산되게 해서, 이 값을 참조하는 다른 모든 코드(`gemini_client.py`, 라우터들)가 "지금 시점의" 활성화 여부를 보게 하기 위함이다. 만약 별도 필드로 뒀다면 `.env` 재로딩 없이는 어차피 프로세스 시작 시점에 고정되니 실익은 없지만, 의도를 코드로 명시한다는 점에서 property가 더 정확한 표현이다.

`kafka_topic`, `kafka_group_id`를 상수로 하드코딩하지 않고 설정값으로 뺀 이유는 이 서비스가 **다른 서비스(target-tracking-service)가 만든 토픽을 구독하는 입장**이라서다 — 토픽 이름이 바뀌거나, 로컬에서 테스트용 별도 토픽에 붙여보고 싶을 때 코드를 안 건드리고 `.env` 한 줄로 대응하게 하려는 목적.

`embedding_dimension: int = 768`은 Gemini `gemini-embedding-001`이 기본으로는 3072차원을 뱉지만 `output_dimensionality` 파라미터로 줄일 수 있다는 걸 이용한 것 — Qdrant 컬렉션의 벡터 크기를 미리 정해야 하는데(`qdrant_store.py`에서 `VectorParams(size=...)`), 768로 고정해두면 저장 공간도 아끼고 컬렉션 스키마도 코드 한 곳(`config.py`)에서만 관리된다. 참고로 `target-tracking-service`의 pgvector 컬럼은 1536차원인데, 이 서비스는 별개의 Qdrant 컬렉션이라 두 값이 같을 필요는 없다 — Java 서비스와 벡터를 공유하지 않으므로 독립적으로 정하면 된다.

## `app/models/schemas.py`

가장 눈여겨볼 부분은 `TargetEvent`:

```python
class TargetEvent(BaseModel):
    targetId: str
    targetType: str
    latitude: float
    longitude: float
    altitude: float
    speed: float
    status: str
```

필드 이름이 Python 관례인 `snake_case`가 아니라 **Java 쪽 `TargetEvent.java`와 똑같이 camelCase**다. 이건 실수가 아니라 의도적인 선택 — 이 클래스는 Kafka로 들어오는 JSON을 그대로 파싱하는 용도이고, 그 JSON은 Spring의 `JsonSerializer`가 Java 필드명 그대로(`targetId`, `targetType`, ...) 직렬화한 것이기 때문이다. pydantic이 alias 기능(`Field(alias=...)`)까지 갈 필요 없이, 필드명을 아예 맞춰버리면 `TargetEvent.model_validate(message.value)` 한 줄로 끝난다. 두 서비스가 별도 레포임에도 "이벤트 스키마"라는 암묵적 계약을 공유하고 있다는 걸 코드 레벨에서 드러내는 장치이기도 하다.

**한 가지 정리한 것**: 처음에는 LangGraph 노드의 중간 결과를 표현할 `SourceChunk`/`ChatRouteResult` pydantic 모델도 `schemas.py`에 만들어뒀는데, 실제로는 그래프 상태를 `TypedDict`(`GraphState`, `app/graph/nodes.py`)로만 다루고 라우터에서도 원시 `dict`를 그대로 SSE로 흘려보내고 있어서 이 두 클래스는 어디서도 import되지 않는 죽은 코드였다. 문서화 과정에서 발견해서 제거했다 — "나중에 API 응답 스펙을 노출할 때 쓰겠지" 같은 미래 대비용 타입은, 실제로 쓰는 곳이 생기기 전까지는 그냥 미사용 코드일 뿐이다.
