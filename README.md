# 🧠 Threat Intel AI Service

[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688.svg?logo=fastapi)](#)
[![Python](https://img.shields.io/badge/Python-3.12-blue.svg?logo=python)](#)
[![LangGraph](https://img.shields.io/badge/LangGraph-routing-1C3C3C.svg)](#)
[![Qdrant](https://img.shields.io/badge/Qdrant-vectordb-DC244C.svg)](#)
[![Kafka](https://img.shields.io/badge/Kafka-shared--topic-black.svg?logo=apachekafka)](#)

## 📌 프로젝트 개요

C4I 시스템([target-tracking-service](../target-tracking-service))에 붙는 **Python 기반 AI 전용 마이크로서비스**입니다.

Java 서비스는 실시간 단일 표적을 **고정된 10개 위협 패턴 지식베이스**와 비교하는 RAG를 이미 수행하고 있습니다.
이 서비스는 그 위에 두 가지를 더합니다.

1. **비정형 문서 RAG** — 위협 인텔리전스 리포트/교리 문서를 업로드하면 청킹·임베딩 후 자연어로 질의응답
2. **실제 탐지 이력 기반 패턴 탐지** — Kafka로 흘러오는 모든 표적 이벤트를 벡터로 적재해, "과거에 이런 패턴이 있었나?" 같은 질문에 고정 지식베이스가 아닌 **실제 이력**으로 답변

두 기능을 하나의 `/chat` 엔드포인트에서 **LangGraph 라우팅**으로 분기합니다.

## 🏗 아키텍처

```
                         ┌──────────────────────────┐
                         │  target-tracking-service   │
                         │  (Spring Boot, Java)       │
                         └────────────┬───────────────┘
                                      │ Kafka: target-tracking topic
                                      │ (독립 consumer group으로 구독)
                                      ▼
                    ┌───────────────────────────────────┐
                    │      threat-intel-ai-service         │
                    │            (FastAPI)                 │
                    │                                       │
  문서 업로드 ──▶ 청킹 ──▶ 임베딩 ──▶ Qdrant[threat_documents]│
                    │                                       │
  Kafka 이벤트 ──▶ 자연어 변환 ──▶ 임베딩 ──▶ Qdrant[target_history]
                    │                                       │
        POST /chat ─┼─▶ LangGraph 질문 분류                  │
                    │     ├─ doc_rag        → threat_documents 검색│
                    │     └─ pattern_search → target_history 검색 │
                    │           │                            │
                    │           ▼                            │
                    │   Gemini 2.5 Flash (SSE 스트리밍 답변)   │
                    └───────────────────────────────────┘
```

## 🛠 기술 스택

| 분류 | 기술 |
|------|------|
| Language | Python 3.12 |
| Framework | FastAPI (async), Uvicorn |
| Orchestration | LangGraph (질문 분류 → 라우팅) |
| Vector DB | Qdrant |
| LLM / Embedding | Google Gemini 2.5 Flash, gemini-embedding-001 |
| Message Queue | Apache Kafka (target-tracking-service와 토픽 공유, aiokafka) |
| Streaming | Server-Sent Events (SSE) |

## 📋 API

### 상태 확인
```http
GET /health
```
```json
{
  "status": "ok",
  "ai_enabled": true,
  "qdrant_connected": true,
  "kafka_consumer_running": true
}
```

### 문서 적재 (RAG용)
```http
POST /ingest/doc
Content-Type: multipart/form-data

file: threat-report.pdf
```
```json
{ "doc_id": "3f9c...", "chunk_count": 12 }
```

### 채팅 (SSE 스트리밍)
```http
POST /chat
Content-Type: application/json

{ "question": "DRONE 자폭 패턴에 대한 교리상 대응 절차는?" }
```
응답은 SSE 이벤트 스트림입니다.
```
event: route
data: {"route": "doc_rag"}

data: {"token": "자폭"}
data: {"token": " 드론"}
...
event: sources
data: [{"text": "...", "score": 0.83, "metadata": {...}}]

event: done
data: {}
```

- 문서/교리 관련 질문 → `doc_rag` (Qdrant `threat_documents` 검색)
- "과거에 이런 표적 있었나?" 류 질문 → `pattern_search` (Qdrant `target_history` 검색, 실제 Kafka 이력 기반)
  - 이 경로에서는 이력을 근거로 정량 등급 평가가 필요하다고 모델이 판단하면 `assess_threat_level` 도구를 직접 호출한다 (Gemini function-calling — 그래프가 강제로 부르는 게 아니라 모델이 결정). 호출되면 `event: tool_call`이 추가로 온다:
    ```
    event: tool_call
    data: {"tool_called": true, "tool_name": "assess_threat_level", "tool_result": "MEDIUM"}
    ```

## 🚀 빠른 시작

```bash
# 1. Gemini API 키 설정 (target-tracking-service와 같은 키 재사용 가능)
cp .env.example .env
echo "GEMINI_API_KEY=AIza..." >> .env

# 2. 공유 Kafka 브로커 주소 설정 (target-tracking-service의 .env와 동일한 값 사용)
#    KAFKA_BOOTSTRAP_SERVERS=<tailscale-ip>:9092

# 3. Qdrant + 앱 기동
docker compose up -d --build

# 4. 상태 확인
curl http://localhost:8000/health

# 5. 문서 적재
curl -X POST http://localhost:8000/ingest/doc -F "file=@threat-report.txt"

# 6. 채팅 (SSE)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "최근 유사한 저고도 고속 접근 사례가 있었나?"}'
```

로컬에서 파이썬으로 바로 돌리려면:
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
(이 경우 Qdrant는 `docker run -p 6333:6333 qdrant/qdrant`로 별도 기동 필요)

> **Python 버전 주의**: `pydantic-core`가 아직 Python 3.14용 사전 빌드 wheel을 제공하지 않아 `python3.14`로 venv를 만들면 설치 단계에서 빌드 실패한다. `python3.12`(또는 3.13)를 명시적으로 지정할 것 — 자세한 실패 로그는 [docs/concepts/07-docker-and-local-verification.md](docs/concepts/07-docker-and-local-verification.md) 참고.

## 📂 구조

```
app/
├── main.py              # FastAPI 앱, lifespan에서 Qdrant 컬렉션 초기화 + Kafka consumer 기동
├── config.py            # 환경변수 설정 (pydantic-settings)
├── routers/
│   ├── health.py
│   ├── chat.py          # POST /chat (SSE)
│   └── ingest.py        # POST /ingest/doc
├── graph/
│   ├── nodes.py         # classify / doc_rag / pattern_search 노드
│   └── router_graph.py  # LangGraph StateGraph 조립
├── rag/
│   ├── chunking.py
│   ├── doc_store.py     # Qdrant `threat_documents` 컬렉션
│   ├── pattern_store.py # Qdrant `target_history` 컬렉션
│   └── qdrant_store.py
├── llm/
│   └── gemini_client.py # 임베딩 + 분류 + 스트리밍 생성, 키 미설정 시 Graceful Degradation
└── kafka/
    └── consumer.py      # target-tracking 토픽 구독 (독립 consumer group)
```

## 📖 관련 문서

- [docs/architecture.md](docs/architecture.md) — LangGraph 라우팅 설계와 Java 서비스와의 역할 분담 상세 설명
- [target-tracking-service/docs/ai-analysis.md](../target-tracking-service/docs/ai-analysis.md) — 기존 Java RAG 위협 분석 시스템
- `docs/concepts/` — 파일 단위 상세 구현 노트
  - [01-config-and-schemas.md](docs/concepts/01-config-and-schemas.md)
  - [02-gemini-client-and-graceful-degradation.md](docs/concepts/02-gemini-client-and-graceful-degradation.md)
  - [03-qdrant-vector-stores.md](docs/concepts/03-qdrant-vector-stores.md)
  - [04-langgraph-routing.md](docs/concepts/04-langgraph-routing.md)
  - [05-kafka-consumer.md](docs/concepts/05-kafka-consumer.md)
  - [06-fastapi-app-and-sse-streaming.md](docs/concepts/06-fastapi-app-and-sse-streaming.md)
  - [07-docker-and-local-verification.md](docs/concepts/07-docker-and-local-verification.md)
  - [08-k3s-cluster-capacity-check.md](docs/concepts/08-k3s-cluster-capacity-check.md) — 지금 클러스터에 이 서비스를 얹을 여유가 있는지 실측한 기록
  - [09-github-actions-ci-and-dockerhub.md](docs/concepts/09-github-actions-ci-and-dockerhub.md) — CI/CD 구성과 Docker Hub 인증 트러블슈팅
  - [10-live-verification-chat-and-ingest.md](docs/concepts/10-live-verification-chat-and-ingest.md) — 실제 클러스터에서 `/chat`/`/ingest/doc` 검증, 발견/수정한 버그 2건
  - [11-tool-calling-node-and-ragas-evaluation.md](docs/concepts/11-tool-calling-node-and-ragas-evaluation.md) — Gemini function-calling 노드, RAGAS 평가, `gemini-flash-latest` 일일 할당량 함정
- `k3s-msa-infrastructure/docs/Threat-Intel-AI-Service-K3s-Deployment.md` — 배포 매니페스트/ArgoCD/Image Updater 등록 기록 (+ `/ai` Ingress path 추가)
- `k3s-msa-infrastructure/docs/Threat-Intel-AI-Service-Cost-and-Resource-Verification.md` — 비용/리소스 실측
