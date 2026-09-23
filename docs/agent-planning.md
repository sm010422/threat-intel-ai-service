# 멀티스텝 플래닝 에이전트 (tool calling 확장)

## 이전 구조의 한계

기존 `assess_threat_node`는 도구를 **딱 하나만, 한 턴만** 호출할 수 있었다. `assess_threat_level` 하나를 모델이 부를지 말지 결정하는 정도가 전부라 "위협 등급을 매기고, 그 등급에 맞는 대응 절차도 알려주고, 지금 대응할 자산 여유가 있는지까지" 같은 질문에는 답할 수 없었다 — 도구가 하나뿐이니 체이닝 자체가 불가능했다.

## 바뀐 구조: 도구 3개 + 멀티턴 루프

```
사용자 질문
    │
    ▼
assess_threat_node (LangGraph 노드)
    │
    ▼
call_with_tools(prompt)  ── ChatSession 시작
    │
    ├─ Gemini: function_call 반환? ──아니오──▶ 종료 (순수 텍스트 응답 = 더 이상 도구 불필요)
    │         │
    │        예
    │         ▼
    │   도구 실행 (동기/비동기 자동 판별) → 결과를 다음 턴 메시지로 전달
    │         │
    │         └──▶ (반복, 최대 MAX_TOOL_STEPS=3회)
    ▼
tool_calls: list[dict]  (0~3개, 순서 보존)
```

세 도구 (`app/graph/tools.py`):

| 도구 | 하는 일 | 데이터 소스 |
|---|---|---|
| `assess_threat_level` | 표적 유형/고도/속도 → 규칙 기반 위협 등급 | 순수 함수 (target-tracking-service와 동일 룰) |
| `lookup_response_procedure` | 위협 패턴 설명 → 대응 절차 검색 | 기존 `threat_documents` Qdrant 컬렉션 (`search_documents` 재사용) |
| `check_intercept_asset_availability` | 표적 유형 → 요격 자산 가용 상태 | **시뮬레이션 데이터** (실제 자산 관리 시스템 연동 없음 — 정적 시드 + READY/COOLDOWN 랜덤) |

모델이 세 도구 중 무엇을, 몇 개를, 어떤 순서로 호출할지는 그래프가 강제하지 않는다. 프롬프트로 "필요한 것만 순서대로 호출하고, 이미 답할 수 있으면 멈춰라"라고 지시하고, Gemini의 function-calling 응답(다음 도구를 부를지 vs 텍스트로 답할지)이 곧 그 판단이다.

## 왜 스텝 수를 3으로 못박았나

이 프로젝트는 Gemini 무료 tier 일일 쿼터를 이미 여러 번 실측으로 소진해봤다(`docs/concepts/11-tool-calling-node-and-ragas-evaluation.md`, target-tracking-service의 `ANALYSIS_COOLDOWN`/`ai.auto-analysis.enabled` 등). 멀티스텝 에이전트는 질문 하나가 최악의 경우 LLM 호출을 여러 번 연쇄로 쓸 수 있는 구조라, 상한이 없으면 모델이 도구 호출을 반복하는 케이스에서 쿼터를 순식간에 태울 위험이 있다. `MAX_TOOL_STEPS = 3`으로 요청 하나당 최악의 경우도 예측 가능한 범위로 묶었다 — 하드웨어 제약은 없지만(순수 코드, 새 인프라 불필요) 비용/쿼터 제약은 실재하기 때문에 적용한 안전장치다.

## SSE 프로토콜 변화

`event: tool_call`이 이제 **0~3번** 올 수 있다 (기존엔 최대 1번). 프론트엔드(`ChatPanel.tsx`)는 매 이벤트를 배열에 누적해서 "N단계: 도구명 → 결과" 형태로 순서대로 렌더링한다.

```
event: route
data: {"route": "pattern_search"}

data: {"token": "..."}
event: tool_call
data: {"tool_called": true, "tool_name": "assess_threat_level", "tool_result": "HIGH"}

event: tool_call
data: {"tool_called": true, "tool_name": "lookup_response_procedure", "tool_result": "..."}

event: sources
data: [...]

event: done
data: {}
```
