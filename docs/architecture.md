# 아키텍처 상세

## 왜 별도 Python 서비스인가

`target-tracking-service`(Spring Boot)는 이미 Spring AI + pgvector로 RAG 위협 분석을 수행한다.
그런데 그 RAG는 다음 한 가지 형태로 고정되어 있다.

> 실시간으로 들어온 표적 1건 → 사전에 시딩해둔 **고정 10개 위협 패턴**과 코사인 유사도 비교 → SITREP 생성

이 서비스가 채우는 빈틈은 두 가지다.

1. **비정형 문서**: 위협 패턴은 코드로 미리 정의된 10개뿐이고, 실제 교리 문서·정보 리포트 같은 텍스트 뭉치를 검색 대상으로 삼지 못한다.
2. **실제 이력**: 비교 대상이 고정 지식베이스일 뿐, 지금까지 실제로 탐지된 표적들의 이력과는 비교하지 않는다. "이번 주에 비슷한 패턴이 몇 번 있었나?" 같은 질문에 답할 수 없다.

Java 진영에서 억지로 확장하기보다, Python/LangChain 생태계가 강점을 가진 비정형 문서 처리·에이전트 라우팅을 별도 마이크로서비스로 분리하는 쪽이 자연스럽다 — Spring AI 자체도 Python LangChain만큼 문서 로더/스플리터 생태계가 두텁지 않다.

## LangGraph 라우팅

`/chat`은 질문을 그대로 LLM에 넣지 않고, 먼저 두 갈래로 분류한다.

```
START
  │
  ▼
classify (Gemini 분류 프롬프트, 실패 시 키워드 휴리스틱 폴백)
  │
  ├─ doc_rag ────────▶ Qdrant[threat_documents] 검색
  │                         │
  └─ pattern_search ─▶ Qdrant[target_history] 검색
                            │
                            ▼
                    context + sources 반환
                            │
                            ▼
              (그래프 밖) Gemini 스트리밍 답변 생성
```

분류를 그래프 밖 단일 LLM 호출로 처리하지 않고 별도 노드로 분리한 이유는, 라우팅 로직(임베딩 대상 컬렉션 선택)과 생성 로직(최종 답변 스트리밍)의 책임을 나눠서 나중에 노드를 더 추가하기 쉽게(예: 규칙 기반 위협 등급 조회 노드 등) 하기 위함이다.

최종 답변 생성은 그래프 안에 넣지 않았다. LangGraph 노드 단위 스트리밍은 SSE와 엮기가 번거로워서, "그래프는 검색까지만 담당하고, 스트리밍 생성은 라우터가 받은 컨텍스트로 직접 수행"하는 방식을 택했다.

## Qdrant 컬렉션

| 컬렉션 | 내용 | 적재 경로 |
|---|---|---|
| `threat_documents` | 업로드된 위협 인텔리전스 문서 청크 | `POST /ingest/doc` |
| `target_history` | Kafka `target-tracking` 토픽으로 들어온 모든 표적 이벤트의 자연어 설명 | Kafka consumer (백그라운드) |

두 컬렉션 모두 Gemini `gemini-embedding-001`을 768차원으로 임베딩한다 (`output_dimensionality=768`).

## Kafka 연동

`target-tracking-service`가 생산하는 동일한 `target-tracking` 토픽을 구독하되, consumer group을 `threat-intel-ai-service`로 분리했다. Kafka는 토픽 하나를 여러 consumer group에 독립적으로 팬아웃하므로, Java 서비스의 `target-tracking-group`과 오프셋을 공유하지 않고 각자 전체 스트림을 받는다.

## Graceful Degradation

`GEMINI_API_KEY`가 없으면:
- `/chat`은 `event: error`만 반환
- `/ingest/doc`은 503
- Kafka consumer는 계속 돌지만 임베딩 실패를 로그로 남기고 다음 메시지로 넘어감

Java 서비스가 API 키 미설정 시 규칙 기반 위협 등급으로 폴백하는 것과 동일한 설계 원칙이다 — AI가 없어도 서비스 자체는 죽지 않는다.
