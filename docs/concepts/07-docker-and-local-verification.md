# 개념 정리 — Dockerfile / docker-compose.yml / 로컬 실측 기록

## `Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`requirements.txt`를 `app/` 코드보다 먼저 `COPY`하고 그 사이에 `pip install`을 끼워 넣은 순서가 핵심이다. Docker 레이어 캐싱은 이전 레이어가 안 바뀌면 캐시를 재사용하는데, 이 순서 덕분에 **의존성이 안 바뀐 채로 코드만 수정했을 때는 `pip install` 레이어가 캐시에서 그대로 재사용되어 빌드가 훨씬 빠르다.** 만약 `COPY . .`로 한 번에 복사했다면 코드 한 줄만 바꿔도 `pip install`부터 다시 돈다.

`python:3.12-slim`으로 버전을 명시적으로 고정한 이유는 로컬 검증 과정에서 실제로 겪은 문제와 직결된다 (아래 "겪은 문제" 참고) — 이 프로젝트의 `requirements.txt`에 박아둔 `pydantic==2.10.4`가 Python 3.14용 사전 빌드 wheel을 아직 제공하지 않아서, 베이스 이미지가 최신 Python을 따라갔다면 컨테이너 빌드 자체가 깨졌을 것이다.

## `docker-compose.yml`

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.12.4
    ports: ["6333:6333", "6334:6334"]
    volumes: ["qdrant_storage:/qdrant/storage"]

  app:
    build: .
    ports: ["8000:8000"]
    env_file: [".env"]
    environment:
      QDRANT_HOST: qdrant
    depends_on: [qdrant]
```

`target-tracking-service`의 `docker-compose.yml`과 다른 점: 그쪽은 Postgres/Redis/Kafka/Zookeeper까지 전부 자체적으로 띄우는 "올인원" 구성인 반면, 이 서비스는 **Qdrant만** 자체적으로 띄우고 Kafka는 `.env`의 `KAFKA_BOOTSTRAP_SERVERS`로 **기존에 떠 있는 공유 브로커**(target-tracking-service의 것, 혹은 실제 클러스터의 것)를 가리키게 했다. Kafka 브로커를 이 레포에서 또 하나 띄우면 완전히 별개의 두 Kafka 클러스터가 생겨서 두 서비스가 같은 토픽을 공유한다는 전제 자체가 깨지기 때문이다.

`QDRANT_HOST: qdrant`를 `environment`에 별도로 얹은 이유 — `.env`의 기본값은 로컬(venv로 직접 실행할 때)을 위해 `localhost`로 두고 싶었는데, docker-compose 네트워크 안에서는 서비스 이름(`qdrant`)이 곧 호스트명이다. `env_file`보다 `environment`가 우선순위가 높다는 compose 규칙을 이용해서, `.env` 파일은 안 건드리고 compose 안에서만 오버라이드했다.

## 겪은 문제와 해결 — venv 만드는 단계에서 막힘

`pip install -r requirements.txt`를 처음 돌렸을 때 이런 에러로 실패했다:

```
error: the configured Python interpreter version (3.14) is newer than PyO3's maximum supported version (3.13)
Error: command ['maturin', 'pep517', 'build-wheel', ...] returned non-zero exit status 1
ERROR: Failed building wheel for pydantic-core
```

원인: 이 맥북의 시스템 기본 `python3`가 3.14였는데, `pydantic==2.10.4`가 의존하는 `pydantic-core`(Rust로 작성, PyO3 바인딩)는 아직 3.14용 사전 빌드 wheel을 안 올려둔 상태라 소스에서 직접 빌드를 시도했고, 그 빌드에 쓰이는 PyO3 버전이 3.14를 지원하지 않아 실패했다.

```bash
which python3.12 python3.11 python3.13
# → /opt/homebrew/bin/python3.12 존재 확인
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # 성공
```

Homebrew로 이미 설치돼 있던 `python3.12`를 명시적으로 지정해서 venv를 다시 만드는 것으로 해결했다. **여기서 확인한 것**: 이 레포의 의존성 스택(FastAPI/pydantic/langgraph 등)은 아직 Python 3.14를 완전히 지원하지 않으므로, `Dockerfile`의 `python:3.12-slim`이나 로컬 개발 시 `python3.12`를 명시적으로 쓰는 걸 README/문서에 남겨둘 필요가 있다 — 시스템 기본 `python3`를 그냥 믿고 쓰면 똑같은 문제에 부딪힌다.

## 겪은 문제 — Docker 데몬 연결 실패 (일시적)

Qdrant를 컨테이너로 띄우려다 `Cannot connect to the Docker daemon at unix:///Users/parksangmin/.colima/default/docker.sock`을 만났다. `colima status`로는 이미 실행 중이라고 나왔는데, 막상 `docker` 커맨드는 소켓 연결에 실패한 상태 — `colima start`(이미 실행 중이라 무시됨) 이후 `docker context ls` / `docker info`를 다시 호출하니 정상적으로 응답했다. 재현 가능한 근본 원인까지는 파고들지 않았고, 일시적인 소켓 준비 지연으로 보고 재시도로 넘어갔다.

## 실제로 검증한 것 (스모크 테스트 로그 기준)

1. **venv 의존성 설치** — Python 3.12로 `requirements.txt` 전체 설치 성공.
2. **모듈 임포트** — `python -c "import app.main"` 성공 (문법/임포트 오류 없음).
3. **Qdrant 없이 기동** — `ensure_collections()`에서 `httpx.ConnectError` → lifespan 실패 → `Application startup failed. Exiting.` **의도한 대로 하드 실패하는 것 확인.**
4. **Qdrant 컨테이너 기동 후 재기동** (`KAFKA_ENABLED=false`):
   - `GET /health` → `{"status":"ok","ai_enabled":false,"qdrant_connected":true,"kafka_consumer_running":false}`
   - `GET /docs` → `200`
   - 서버 로그에 `PUT /collections/threat_documents`, `PUT /collections/target_history` 호출이 찍히고, `curl localhost:6333/collections`로 실제 두 컬렉션이 생성된 것 확인.
5. **AI 키 없이 degradation 경로**:
   - `POST /chat` → `event: error\ndata: {"message": "GEMINI_API_KEY not configured"}` (SSE로 정상 응답, 서버는 안 죽음)
   - `POST /ingest/doc` → `503`
6. **git 커밋** — `.venv/`, `.env`, `qdrant_storage/` 등이 `.gitignore`로 정상 제외된 상태로 소스만 커밋됨을 `git status --short` / `git add -A` 후 diff로 확인.

## 아직 실측하지 못한 것 (다음 검증 단계로 남음)

- 실제 `GEMINI_API_KEY`를 넣은 상태에서 `/chat` 스트리밍, `classify_question`의 LLM 분류, `/ingest/doc`의 실제 임베딩·색인 경로.
- 실제 Kafka 브로커에 붙여서 `target-tracking-service`가 만든 이벤트가 `target_history` 컬렉션에 실제로 색인되는지.
- k3s 클러스터(worker2)에 실제로 배포했을 때의 리소스 사용량 — `docs/concepts/08-k3s-cluster-capacity-check.md`의 추정치는 로컬 idle 기동 기준이라, 실제 트래픽 하에서 검증이 필요하다.
