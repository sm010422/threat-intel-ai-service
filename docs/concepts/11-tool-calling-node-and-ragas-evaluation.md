# 개념 정리 — Gemini function-calling 노드와 RAGAS 평가

`docs/concepts/04-langgraph-routing.md`에서 정리한 그래프는 "분류 → 검색" 2단계뿐이었다. 이 문서는 그 위에 실제 tool-calling(함수 호출) 노드를 하나 추가하고, 그 결과 전체 파이프라인의 RAG 품질을 RAGAS로 실측한 과정을 다룬다.

## 1. `app/graph/tools.py` — 규칙 기반 등급을 "도구"로 노출

```python
ASSESS_THREAT_LEVEL_DECLARATION = genai.protos.FunctionDeclaration(
    name="assess_threat_level",
    description="표적 유형, 고도(m), 속도(km/h)를 입력받아 규칙 기반 위협 등급...",
    parameters=genai.protos.Schema(...),
)

def assess_threat_level(target_type: str, altitude: float, speed: float) -> str:
    if target_type.upper() == "MISSILE":
        return "CRITICAL"
    ...
```

`assess_threat_level` 자체는 `target-tracking-service`(Java)의 `ThreatAnalysisService`가 쓰는 것과 완전히 같은 규칙 테이블이다. 이걸 프롬프트 문자열에 규칙을 나열해서 "이 규칙대로 판단해"라고 시키는 대신, **Gemini의 function-calling API에 실제 Python 함수로 등록**했다 — 차이가 크다:

- 프롬프트에 규칙을 박아넣으면 LLM이 그 규칙을 "따르는 척"하며 텍스트로 답을 생성한다 (환각 가능성이 있고, 계산이 틀릴 수 있다).
- 도구로 등록하면 LLM은 규칙을 계산하지 않는다 — **"이 상황에서 도구를 부를지 말지"만 판단**하고, 실제 계산은 결정론적인 Python 코드가 한다. 결과가 항상 정확하다는 게 보장된다 (동일 입력 → 항상 동일 출력).

## 2. `call_with_tools` — 모델이 실제로 결정한다

```python
def call_with_tools(prompt: str) -> dict:
    response = _get_tool_model().generate_content(prompt)
    part = response.candidates[0].content.parts[0]
    function_call = getattr(part, "function_call", None)

    if function_call and function_call.name == "assess_threat_level":
        args = dict(function_call.args)
        result = assess_threat_level(**...)
        return {"tool_called": True, ...}
    return {"tool_called": False, ...}
```

`_get_tool_model()`은 `_get_chat_model()`과 별도의 `GenerativeModel` 인스턴스다 — `tools=[THREAT_ASSESSMENT_TOOL]`을 생성자에 넘겨야 하고, 이 바인딩은 인스턴스 생성 시점에 고정되기 때문에 도구가 필요 없는 일반 스트리밍 생성(`generate_stream`)과는 모델 객체를 분리했다.

**핵심은 `if function_call and ...`다.** 이 함수를 호출한다고 도구가 항상 실행되는 게 아니다 — Gemini가 입력 프롬프트를 보고 "이 질문엔 정량 평가가 필요 없다"고 판단하면 `function_call`이 `None`이고, 그냥 아무 결과 없이 넘어간다. 실제로 배포 후 확인해보니:

- "과거에 DRONE-001 표적이 탐지된 이력이 있었나?" → 도구 호출 **안 함** (이력 존재 여부를 묻는 질문이라 정량 등급이 불필요하다고 판단)
- "과거 DRONE-001 탐지 이력을 보고 위협 등급을 정량적으로 평가해줘" → 도구 호출 **함** (`assess_threat_level` → `MEDIUM`)

같은 그래프, 같은 노드인데 질문에 따라 실행 경로가 달라진다 — 그래프가 강제로 분기하는 게 아니라 모델의 판단이 실행을 좌우한다는 뜻이고, 이게 "hardcoded branch"와 "genuine tool-calling"의 차이다.

## 3. 그래프에 노드를 붙인 위치

```python
graph.add_edge("pattern_search", "assess_threat")
graph.add_edge("assess_threat", END)
graph.add_edge("doc_rag", END)  # doc_rag는 그대로 END로
```

`assess_threat` 노드는 `pattern_search` 브랜치에만 붙였다. `doc_rag`(교리 문서 검색)에는 "표적의 위협 등급"이라는 개념 자체가 적용이 안 되기 때문 — 문서에는 특정 표적의 고도/속도 값이 없다. 노드를 두 브랜치 다 거치게 하는 것보다, 그 노드가 의미 있는 브랜치에만 붙이는 게 더 정확한 그래프 설계라고 판단했다.

`assess_threat_node`는 `sources[0]`(가장 유사도 높은 검색 결과) 하나만 골라서 도구 호출 여부를 판단한다 — 이력이 여러 개 나와도 "가장 관련성 높은 표적"을 대표로 평가하는 방식.

## 4. 이 과정에서 발견한 별개의 프로덕션 버그

이 노드를 실제 배포 환경에서 검증하던 중(직접적인 관련은 없지만) 두 가지를 더 발견해서 고쳤다.

**① `pattern_search`의 metadata에 altitude/speed가 안 들어있었다.** `pattern_store.py`의 Qdrant payload에는 이미 저장돼 있었는데, `search_patterns()`가 리턴하는 `metadata` dict를 조립할 때 `target_id`/`target_type`/`observed_at`만 뽑고 altitude/speed를 빠뜻렸다 — `assess_threat_node`가 등급을 계산하려면 이 두 값이 필수라서, 이 노드를 만들면서 비로소 이 누락을 발견했다. 값 자체는 이미 저장돼 있었으니 리턴 딕셔너리에 두 줄만 추가하면 됐다.

**② `gemini-flash-latest`가 이 계정에서는 `gemini-3.6-flash`로 연결되는데, 무료 티어 일일 한도가 20회뿐이었다.** 6절에서 자세히 다룬다.

## 5. RAGAS 평가 — 그리고 그 과정에서 잡아낸 진짜 장애

`eval/evaluate_rag.py`는 실제 `/chat` 엔드포인트를 SSE로 호출해서 `(질문, 답변, 검색된 컨텍스트)` 세 쌍을 모으고, **reference-free 지표 두 개**로 평가한다 — 정답 라벨링 없이 바로 돌릴 수 있다는 게 포인트:

- `faithfulness`: 답변이 검색된 컨텍스트를 벗어나 지어낸 내용이 있는지 (0~1)
- `answer_relevancy`: 답변이 실제로 질문과 관련 있는지 (0~1)

### 5.1 첫 실행 — RAGAS가 자기 할 일을 정확히 했다

첫 실행에서 모든 질문의 두 지표가 **정확히 0.0**으로 나왔다. 처음엔 "채점 스크립트가 잘못됐나" 싶었는데, `eval/last_run.json`을 열어보니 원인이 명확했다:

```json
{"user_input": "...", "retrieved_contexts": ["... 실제 검색된 문서 ..."], "response": "(응답 없음)"}
```

**검색(`retrieved_contexts`)은 정상이었는데 생성(`response`)이 전부 비어 있었다.** 원인은 4절에서 발견한 그 버그 — 배포된 pod가 아직 `gemini-flash-latest`(→`gemini-3.6-flash`, 일일 20회 한도)를 쓰고 있었고, 그 한도를 RAGAS 자체 평가 호출(judge 모델도 같은 걸 썼다)이 먼저 소진해버려서, 실제 `/chat` 호출의 생성 단계가 전부 실패해 빈 답변만 쌓인 것. **RAGAS는 정확히 "생성이 실패한 상태"를 0.0으로 잡아낸 것이지, 스크립트 버그가 아니었다** — reference-free 지표라도 이런 "완전 실패"는 정확히 걸러낸다는 걸 실측으로 확인한 셈.

### 5.2 원인 수정 후 재실행 — 정상적인 점수

`app/config.py`의 `gemini_chat_model` 기본값을 `gemini-flash-lite-latest`로 바꾸고(같은 키로 실제 응답되는 것과 function-calling까지 되는 것을 먼저 직접 확인한 뒤) 재배포하고 다시 돌리니:

```
질문                                          faithfulness  answer_relevancy
저고도 자폭 드론이 접근할 때 대응 절차가 어떻게 되나?              1.00          0.87
군집(스웜) 형태로 드론이 접근하면 왜 위험한가?                    0.75          0.86
과거에 DRONE-001 표적이 탐지된 이력이 있었나?                    0.80          0.91
```

세 질문 다 컨텍스트에 충실하고(0.75 이상) 질문과 관련성도 높다(0.86 이상) — RAG 파이프라인이 실제로 잘 동작한다는 걸 숫자로 확인한 것.

### 5.3 RAGAS 자체의 함정 — 병렬 호출이 무료 티어를 순식간에 태운다

RAGAS의 `evaluate()`는 기본적으로 최대 16개 worker로 LLM judge 호출을 병렬 처리한다. `faithfulness` 하나만 해도 내부적으로 "답변을 주장(claim) 단위로 분해 → 각 주장을 컨텍스트와 대조" 하는 여러 번의 LLM 호출이 필요해서, 질문 3개 × 지표 2개만 돌려도 실제 LLM 호출 수는 수십 번이 된다. Gemini 무료 티어처럼 요청 한도가 낮은 환경에서는 이게 곧바로 429(`ResourceExhausted`)를 유발한다.

```python
JUDGE_RUN_CONFIG = RunConfig(max_workers=1, max_wait=30, max_retries=6, timeout=300)
...
result = evaluate(dataset, metrics=[...], run_config=JUDGE_RUN_CONFIG)
```

`max_workers=1`로 완전히 직렬화하고 재시도 대기 시간을 넉넉히 잡아서, 느리지만 확실하게 끝나도록 했다 (3문항 평가에 약 2~3분 소요).

## 6. 별도로 남긴 흔적 — `gemini-flash-latest`의 함정

`genai.list_models()`로는 카탈로그에 존재해서 "쓸 수 있어 보이는" 모델이라도, 실제 무료 티어 할당량이 계정/모델 조합마다 다르다는 걸 이번에 두 번째로 겪었다 (`docs/concepts/10-live-verification-chat-and-ingest.md`의 `gemini-2.5-flash` 404 사례가 첫 번째). `gemini-flash-latest`라는 "최신을 자동으로 가리키는" 별칭이 실제로는 `gemini-3.6-flash`(무료 일일 20회)로 연결된다는 건 API 응답의 에러 메시지(`quota_id: GenerateRequestsPerDayPerProjectPerModel-FreeTier`)를 직접 읽어야 알 수 있었다 — 별칭이 가리키는 실제 모델과 그 모델의 할당량은 API 문서만 봐서는 예측하기 어렵고, **실제로 호출해서 에러 메시지를 읽는 게 가장 빠른 확인 방법**이라는 걸 다시 확인했다.
