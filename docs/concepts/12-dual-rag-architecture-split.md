# 개념 정리 — 두 개로 갈라진 RAG 파이프라인

"지금 RAG 구현이 어떻게 돼 있냐"는 질문에 코드를 다시 읽다가 확인한 사실. `target-tracking-service`(Java)와 `threat-intel-ai-service`(Python) 둘 다 "RAG"를 하고 있다고 말할 수 있는데, 실제로는 **벡터 DB 엔진도 다르고, 같은 Gemini 임베딩 모델을 쓰면서도 출력 차원이 달라서 두 저장소가 물리적으로 호환되지 않는 완전히 독립된 파이프라인**이다. 처음부터 이렇게 설계한 게 아니라, 두 서비스가 각자 따로 만들어지면서 자연히 갈라진 결과다.

## 한눈에 비교

| | target-tracking-service (Java) | threat-intel-ai-service (Python) |
|---|---|---|
| 벡터 DB | pgvector (Postgres) | Qdrant |
| 임베딩 모델 | `gemini-embedding-001` | `gemini-embedding-001` (같은 모델) |
| 임베딩 차원 | **1536** | **768** |
| 색인 대상 | 고정 위협 패턴 10개 (손으로 작성) | 업로드 문서 + 실제 탐지 이력 (2개 컬렉션) |
| 색인 시점 | 앱 기동 시 1회 (idempotent) | 문서는 수동 업로드 시, 이력은 Kafka 이벤트마다 |
| 검색 라우팅 | 없음 (단일 검색) | LangGraph가 질문 유형별로 컬렉션 분기 |
| Agentic 요소 | 없음 | function-calling으로 도구 호출 여부까지 LLM이 결정 |

## A) target-tracking-service — pgvector, 고정 지식베이스

`ThreatKnowledgeInitializer`(`ApplicationRunner`, `@Order(100)`)가 앱 기동 시 손으로 작성한 위협 패턴 10개를 pgvector에 한 번만 시딩한다. 예:

```
표적유형: DRONE, 행동패턴: 저속(30-80km/h) 저고도(50-150m) 경계선 순찰, 위협등급: MEDIUM,
분석: 광학/열화상 센서 탑재 정찰 드론 추정. ...
```

`vector_store` 테이블에 `metadata->>'source' = 'c4i-threat-kb'`인 행이 이미 있는지로 idempotent 체크를 하기 때문에, 재배포/재시작해도 중복 삽입되지 않는다. `GEMINI_API_KEY`가 `PLACEHOLDER`면 이 초기화 자체를 건너뛴다.

검색은 `ThreatAnalysisService.analyze()`가 담당한다. 현재 표적을 자연어 문장(`"표적ID=..., 유형=..., 속도=..."`)으로 만들고, `vectorStore.similaritySearch()`(topK=3, similarityThreshold=0.5)로 이 10개 패턴 중 가장 비슷한 걸 찾은 뒤, 그걸 컨텍스트로 Gemini에게 SITREP을 쓰게 시킨다.

**즉 이건 "실시간 탐지 이력 검색"이 아니라 "미리 정의된 교리/패턴과의 매칭"이다.** 규칙 기반 위협 등급(`calculateRuleBasedThreatLevel`)을 보강하는 참고자료 역할에 가깝다.

## B) threat-intel-ai-service — Qdrant, 컬렉션 2개 + LangGraph 라우팅

이쪽은 실제로 동적으로 자라는 데이터를 다룬다.

**컬렉션 1: `threat_documents`** — `/ingest/doc`(`app/routers/ingest.py`)로 PDF/txt를 업로드하면 `chunk_text()`(`app/rag/chunking.py`, LangChain `RecursiveCharacterTextSplitter`, chunk_size=800/overlap=100)로 청크를 쪼갠 뒤 청크마다 임베딩해서 저장한다 (`app/rag/doc_store.py`의 `ingest_document`). 진짜 문서 기반 RAG.

**컬렉션 2: `target_history`** — Kafka consumer(`app/kafka/consumer.py`)가 탐지된 표적 이벤트를 자연어로 묘사해서 임베딩·저장한다(`app/rag/pattern_store.py`의 `upsert_target_event`). Java 쪽 고정 패턴과 달리 **실제로 관측된 이력**을 대상으로 한다. 다만 이 자동 색인은 필터링/쿨다운이 전혀 없어서 ADS-B 피드가 흘려보내는 모든 이벤트마다 Gemini 임베딩을 호출했고, 이게 Gemini 무료 tier 일일 임베딩 한도(1000건)를 실제로 소진시킨 주범이었다 — 지금은 `settings.auto_index_enabled`(기본 `false`)로 게이팅돼 있다 (자세한 경위: [target-tracking-service의 07번 문서](https://github.com/sm010422/target-tracking-service/blob/main/docs/concepts/07-gemini-quota-incident-and-on-demand-ai-analysis.md)).

**라우팅**은 `app/graph/router_graph.py`의 LangGraph `StateGraph`가 담당한다:

```
START → classify → (조건부 분기) → doc_rag ────────────→ END
                                  → pattern_search → assess_threat → END
```

- `classify` 노드(`app/llm/gemini_client.py`의 `classify_question`)가 질문을 LLM으로 분류하고, LLM이 실패하면 키워드 휴리스틱("이력", "과거", "탐지된" 등)으로 폴백한다.
- `pattern_search`로 간 경우에만 `assess_threat` 노드가 붙는다 — 문서 RAG 질의엔 "표적 위협 등급"이라는 개념 자체가 안 맞기 때문. 여기서 Gemini의 진짜 function-calling(`call_with_tools`)이 `assess_threat_level` 도구를 부를지 말지를 모델 스스로 결정한다(그래프가 강제로 부르는 게 아님).

## 임베딩 차원 불일치가 의미하는 것

두 서비스 다 `gemini-embedding-001`을 쓰지만, Java는 1536차원, Python은 `output_dimensionality=768`로 호출한다. **같은 모델을 쓴다고 데이터를 공유할 수 있는 게 아니다** — 벡터 크기 자체가 다르니 한쪽 벡터를 다른 쪽 컬렉션에 넣는 것 자체가 불가능하다. 이건 의도된 아키텍처 결정이 아니라, 두 서비스가 서로 다른 시점에 독립적으로 만들어지면서 각자 편한 차원을 골랐기 때문에 생긴 결과다.

## 통합할 필요가 있는가

지금 당장은 아니라고 판단한다. 두 파이프라인이 실제로 다른 역할을 하고 있기 때문이다 — Java 쪽은 규칙 기반 등급을 보강하는 고정 근거 자료고, Python 쪽은 실제 이력/문서를 대상으로 한 실험적 동적 RAG다. 억지로 하나의 벡터 DB로 합치면 "규칙 엔진의 안정적인 참고자료"와 "계속 자라나는 실시간 이력"이라는 서로 다른 요구사항이 충돌할 여지가 있다.

만약 나중에 통합한다면, Qdrant(이미 실시간 이력을 다루는 쪽) 하나로 몰고 Java 서비스가 REST로 조회하는 방향이 pgvector를 Python 쪽에 새로 붙이는 것보다 자연스러울 것이다 — 다만 그 경우 임베딩 차원을 하나로 맞추는 재색인 작업(기존 pgvector 데이터를 768차원으로 다시 임베딩)이 선행돼야 한다.

## 관련 문서

- `docs/concepts/03-qdrant-vector-stores.md` — Qdrant 컬렉션 구조 최초 정리
- `docs/concepts/04-langgraph-routing.md` — LangGraph 라우팅 최초 구현
- [target-tracking-service `docs/concepts/03-dashboard-ai-integration.md`](https://github.com/sm010422/target-tracking-service/blob/main/docs/concepts/03-dashboard-ai-integration.md) — Java 쪽 AI 위협분석 최초 구현
- [target-tracking-service `docs/concepts/07-gemini-quota-incident-and-on-demand-ai-analysis.md`](https://github.com/sm010422/target-tracking-service/blob/main/docs/concepts/07-gemini-quota-incident-and-on-demand-ai-analysis.md) — `target_history` 자동 색인이 쿼터를 소진시킨 경위
