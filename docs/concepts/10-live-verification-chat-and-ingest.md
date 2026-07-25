# 개념 정리 — 실제 클러스터에서 `/chat`, `/ingest/doc` 검증

`docs/concepts/07-docker-and-local-verification.md`가 로컬 스모크 테스트(Qdrant만 붙이고 AI 키 없이)를 다뤘다면, 이 문서는 **실제 k3s 클러스터에 배포된 pod에 대고, 진짜 Gemini 키로** 두 엔드포인트를 호출한 기록이다. 이 과정에서 코드 버그 두 개를 실제로 발견해서 고쳤다.

## 테스트 절차

```bash
kubectl port-forward -n c4i svc/threat-intel-ai-service 18000:8000 &

# 1. 문서 적재 - 자폭 드론 대응 교리 샘플 리포트
curl -X POST http://localhost:18000/ingest/doc -F "file=@sample-threat-report.txt"
# → {"doc_id":"71978c9b-...","chunk_count":1}

# 2. doc_rag 라우팅 기대 질문
curl -N -X POST http://localhost:18000/chat \
  -d '{"question": "저고도 자폭 드론이 접근할 때 대응 절차가 어떻게 되나?"}'

# 3. pattern_search 라우팅 기대 질문
curl -N -X POST http://localhost:18000/chat \
  -d '{"question": "과거에 DRONE-001 표적이 탐지된 이력이 있었나?"}'
```

## 결과 1 — 라우팅과 검색은 정확히 작동했다

두 질문 모두 LangGraph 분류가 의도대로 갈렸다.

```
질문1 → event: route data: {"route": "doc_rag"}
      → sources: 방금 적재한 sample-threat-report.txt 청크 1개, score 0.81

질문2 → event: route data: {"route": "pattern_search"}
      → sources: DRONE-001 관련 과거 탐지 이력 3건, score 0.73~0.74
        (Kafka로 색인된 실제 위도/경도/고도/속도 값 그대로)
```

`pattern_search`가 반환한 세 건이 실제로 앞서 `threat-intel-ai-service` consumer group이 소비한 30개 이벤트 중 `DRONE-001` 것들이라는 걸 `observed_at` 타임스탬프로 교차 확인했다 — 이력 탐지가 문서상의 설계(`docs/architecture.md`)대로 실제 Kafka 이력을 근거로 응답한다는 걸 처음으로 end-to-end 확인한 것.

## 결과 2 — 발견한 버그 ① `gemini-2.5-flash` 사용 불가

두 질문 다 `event: route` → `event: sources`로 바로 넘어가고 **토큰 스트림이 한 글자도 없었다.** pod 로그를 보니:

```
google.api_core.exceptions.NotFound: 404 This model models/gemini-2.5-flash
is no longer available to new users. Please update your code to use a
newer model for the latest features and improvements.
```

`genai.list_models()`로 이 API 키가 실제로 쓸 수 있는 모델 목록을 조회해보니 `models/gemini-2.5-flash` 자체는 카탈로그에 여전히 나열되지만, **이 계정(new user 취급)으로는 실제 생성 호출이 거부**되는 상태였다. 카탈로그에 있다고 해서 그 키로 호출 가능하다는 보장이 아니라는 걸 실측으로 확인한 셈.

`gemini-flash-latest`(Google이 제공하는 "현재 권장 flash 모델"을 가리키는 별칭)로 직접 `generate_content` 호출을 먼저 테스트해서 성공을 확인한 뒤 `app/config.py`의 기본값을 교체했다:

```python
# Before
gemini_chat_model: str = "gemini-2.5-flash"
# After
gemini_chat_model: str = "gemini-flash-latest"
```

`-latest` 별칭을 쓴 이유: 특정 버전 이름을 하드코딩하면 이번처럼 Google이 구버전을 신규 계정에서 차단할 때마다 코드를 고쳐야 한다. 별칭은 Google 쪽에서 가리키는 대상을 바꿔주므로 이런 종류의 deprecation에 더 안전하다 — 다만 "지금 정확히 어떤 모델이 응답하는지"를 고정할 수 없다는 트레이드오프는 있다 (재현성이 중요해지면 그때 특정 버전으로 pin하는 게 맞다).

## 결과 3 — 발견한 버그 ② 백그라운드 스레드 예외가 조용히 삼켜짐

버그 ①만으로는 "왜 클라이언트가 아무 에러도 못 보고 그냥 토큰 없이 끝나는지"가 설명이 안 됐다. `routers/chat.py`의 원래 코드:

```python
def produce() -> None:
    try:
        for token in generate_stream(prompt):
            loop.call_soon_threadsafe(queue.put_nowait, token)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)   # 예외가 나도 그냥 종료 신호만 보냄
```

`generate_stream()`이 예외를 던지면 `except`가 없으니 `produce()` 자체가 예외로 끝나는데, 이건 `loop.run_in_executor(None, produce)`가 리턴한 `Future`에 담긴다. **그 Future를 아무도 `await`하지 않았기 때문에 예외가 그냥 사라졌다** — asyncio가 나중에 가비지 컬렉션 시점에 "Future exception was never retrieved"라고 로그만 남기고, 클라이언트 입장에서는 `route` 이벤트 이후 토큰 없이 바로 `sources`/`done`으로 넘어가서 마치 "빈 답변이 정상 응답인 것처럼" 보였다.

고친 방식 — 큐를 통해 예외를 명시적으로 컨슈머 쪽에 전달:

```python
queue: asyncio.Queue[str | BaseException | None] = asyncio.Queue()

def produce() -> None:
    try:
        for token in generate_stream(prompt):
            loop.call_soon_threadsafe(queue.put_nowait, token)
    except Exception as exc:
        loop.call_soon_threadsafe(queue.put_nowait, exc)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)

# 컨슈머 쪽
while True:
    item = await queue.get()
    if item is None:
        break
    if isinstance(item, BaseException):
        yield f"event: error\ndata: {json.dumps({'message': str(item)})}\n\n"
        break
    yield f"data: {json.dumps({'token': item})}\n\n"
```

큐 아이템 타입을 `str | BaseException | None` 세 가지로 넓혀서, "정상 토큰 / 에러 / 종료"를 명확히 구분했다. 에러가 나도 그 뒤에 `sources`/`done`은 그대로 내려준다 — 검색 자체는 성공했으니, 생성이 실패해도 "무엇을 근거로 찾으려 했는지"는 클라이언트에게 남겨주는 게 낫다고 판단했다.

**교훈**: `asyncio.get_running_loop().run_in_executor(...)`로 스레드에 작업을 넘기고 그 Future를 버리면, 그 안에서 난 예외는 기본적으로 아무 데도 보고되지 않는다. 로그에만 애매하게 남거나(`Future exception was never retrieved`), 이번 경우처럼 API 응답 자체가 "에러 없이 그냥 비어있는" 형태로 클라이언트에 전달되어 버그를 알아채기 더 어려워진다.

## 재배포 후 재검증

두 수정을 커밋(`70650ff`)하고 push하니 CI가 자동으로 새 이미지를 빌드해서 Docker Hub에 올렸고, ArgoCD Image Updater가 약 2분 폴링 주기 안에 새 digest를 감지해서 `k3s-msa-infrastructure`에 write-back 커밋을 남기고 pod를 새로 롤아웃했다 (강제로 기다리지 않고 `kubectl patch application ... -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'`로 즉시 반영시켰다). 새 pod가 `1/1 Running`이 된 뒤 같은 질문을 다시 호출한 실제 응답:

```
event: route
data: {"route": "doc_rag"}

data: {"token": "저고도 자폭 드론이"}
data: {"token": " 접근할 때의 대응 절차는 다음과 같이 4단계로 진행됩니다.\n\n* **1단계:** 저고"}
data: {"token": "도 레이더 및 EO/IR 센서로 조기 탐지하고 접근 벡터를 산출합니다.\n*"}
data: {"token": " **2단계:** 전자전 자산(GNSS/RF 재밍)을 사용하여 유도 신호 교란"}
data: {"token": "을 시도합니다.\n* **3단계:** 재밍 실패 시 근접 방어 화기(CIWS급)"}
data: {"token": "로 요격합니다.\n* **4단계:** 요격 실패 시 핵심 자산 방호 태세로 전환하고 인원을"}
data: {"token": " 대피시킵니다."}

event: sources
data: [{"text": "[위협 인텔리전스 리포트 - 저고도 자폭 드론 대응 교리] ...", "score": 0.8106016, ...}]

event: done
data: {}
```

적재한 문서의 "1단계 조기 탐지 → 2단계 전자전 재밍 → 3단계 근접 화기 요격 → 4단계 방호 태세 전환" 4단계를 그대로, 문서에 없는 내용을 지어내지 않고 요약해서 답했다 — 프롬프트(`_build_prompt`)의 "문서에 없는 내용은 추측하지 말고 모른다고 답하라" 지시가 실제로 지켜지는 것도 함께 확인했다.

`pattern_search` 경로도 같은 방식으로 재확인했다. 질문 2("과거에 DRONE-001 표적이 탐지된 이력이 있었나?")에 대한 토큰을 이어붙인 실제 답변:

```
제시된 검색 컨텍스트를 바탕으로 답변드립니다.

**1. 과거 탐지 이력 유무**
네, 과거에 DRONE-001 표적이 탐지된 이력이 총 3건 확인됩니다.

* 이력 1: 고도 약 628.61m, 속도 약 230.47km/h (위도 35.0029..., 경도 127.8742...)
* 이력 2: 고도 약 960.67m, 속도 약 51.19km/h (위도 34.6101..., 경도 128.1111...)
* 이력 3: 고도 약 295.62m, 속도 약 228.16km/h (위도 34.0267..., 경도 127.0709...)

**2. 패턴 반복 추세**
동일한 표적 ID(DRONE-001) 및 유형(DRONE)으로 서로 다른 위치와 비행 조건(고도·속도)에서
모두 'DETECTED(탐지됨)' 상태로 수집된 기록이 다수 존재합니다. 이는 해당 표적에 대한
유사 탐지 패턴이 지속적으로 반복되고 있는 추세임을 보여줍니다.
```

`sources`로 받은 세 벡터 검색 결과(위도/경도/고도/속도 원본값)를 그대로 근거로 인용하면서, 프롬프트의 "유사 패턴이 반복되는 추세인지 언급하라" 지시대로 추세 판단까지 포함한 응답을 만들어냈다 — `doc_rag`/`pattern_search` 두 경로 모두 수정 후 정상 동작을 실측으로 확인 완료.
