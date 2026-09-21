# 개념 정리 — 리랭커(Reranker) 추가

## 1. 문제: 벡터 유사도 top_k를 그대로 프롬프트에 넣고 있었다

`doc_store.search_documents`/`pattern_store.search_patterns`는 지금까지 Qdrant `query_points`가 반환한 상위 `top_k`(기본 3)개를 그대로 컨텍스트로 썼다. 벡터 코사인 유사도만으로 순위를 정하면, 의미적으로는 질문과 가깝지만 실제로 답을 구성하는 데는 덜 중요한 청크가 상위에 올라오는 경우가 실무에서 흔하다 — 벡터 하나(쿼리)와 벡터 하나(문서)의 거리만 보는 bi-encoder 방식의 구조적 한계다. 이걸 교정하는 표준적인 2단계 검색 패턴이 "**넓게 뽑고(retrieve) → 질문-문서 쌍을 직접 보고 재정렬(rerank)해서 좁힌다**"이고, 이번에 이 두 번째 단계를 추가했다.

## 2. 왜 로컬 cross-encoder(`bge-reranker` 등)가 아니라 API인가

`bge-reranker-base`류의 오픈소스 리랭커는 cross-encoder라 `torch`/`transformers` 런타임 위에 모델을 직접 로드해야 한다. 그런데 이 서비스 파드의 메모리 한도는 `apps/threat-intel-ai-service/deployment.yaml`(k3s-msa-infrastructure 리포) 기준 **384Mi**이고, 뜨는 노드(`k3s-worker2`)도 GPU 없는 1 core/1.75GB 워커다(`k3s-msa-infrastructure`의 `docs/Multipass-Operations-Guide.md` 1절 인벤토리 참고). `torch`+`transformers`를 임포트하는 것만으로 런타임이 수백MB를 잡고, 거기에 모델 가중치(수백MB, fp32면 1GB 근처)까지 얹으면 384Mi를 사실상 넘긴다 — 정확한 수치를 실측한 건 아니지만, 이미 이 크기의 메모리 한도로 파드를 운영해본 경험상(`Threat-Intel-AI-Service-Cost-and-Resource-Verification.md`) 안전 마진이 없다고 판단했다.

이 서비스는 애초에 임베딩/생성 둘 다 Gemini API 호출로 처리하고 로컬 모델을 하나도 안 띄우는 구조다 (`app/llm/gemini_client.py`). 같은 원칙을 리랭커에도 적용해 **Cohere Rerank API**(`rerank-v3.5`, 다국어 지원 — 한국어 포함)를 붙였다. 파드 쪽 추가 부담은 HTTP 호출 하나와 `cohere` SDK(순수 클라이언트, torch 의존성 없음) 뿐이다.

### 실제로 확인한 것

`cohere` 패키지를 `.venv`에 임시로 설치해서(작업 후 삭제) `ClientV2.rerank()`의 실제 시그니처와 응답 스키마를 코드 작성 전에 직접 확인했다:

```python
>>> inspect.signature(cohere.ClientV2.rerank)
(self, *, model: str, query: str, documents: Sequence[str], top_n: Optional[int] = ...,
 return_documents: Optional[bool] = ..., max_tokens_per_doc: Optional[int] = ..., ...)
 -> V2RerankResponse

>>> V2RerankResponse.model_fields.keys()
dict_keys(['id', 'results', 'meta'])
>>> V2RerankResponseResultsItem.model_fields.keys()
dict_keys(['document', 'index', 'relevance_score'])
```

`documents`에 문자열 리스트를 그대로 넘기면 되고(별도 dict 래핑 불필요), 결과의 `index`는 **입력 리스트 기준 원래 인덱스**, `relevance_score`가 재정렬 점수라는 걸 이걸로 확정했다. API 키가 없어서 실제 호출까지는 못 해봤고, 여기까지만 검증됐다 — 4절 한계 참고.

## 3. 구현

### 3.1 `app/rag/reranker.py` (신규)

```python
def rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    if not settings.rerank_enabled or len(candidates) <= 1:
        return candidates[:top_n]

    try:
        response = _get_client().rerank(
            model=settings.cohere_rerank_model,
            query=query,
            documents=[c["text"] for c in candidates],
            top_n=min(top_n, len(candidates)),
        )
    except Exception:
        return candidates[:top_n]

    return [{**candidates[result.index], "score": result.relevance_score} for result in response.results]
```

두 가지 지점에서 조기 반환한다:

- **`rerank_enabled`가 `False`(= `COHERE_API_KEY` 미설정)일 때** — API 호출 자체를 안 하고 `candidates[:top_n]`. `app/llm/gemini_client.py`가 `GEMINI_API_KEY` 없을 때 취하는 것과 동일한 graceful-degradation 원칙.
- **후보가 1개 이하일 때** — 순서를 바꿀 대상 자체가 없으니 API 호출 자체가 낭비다. 무료 API 호출 한도를 아낀다는 실용적 이유도 있다.

`except Exception`으로 폭넓게 잡는 것도 의도적이다 — 네트워크 오류든, Cohere 쪽 쿼터 소진(429)이든, 응답 스키마가 예상과 다르든, **리랭커의 실패가 `/chat` 전체를 죽여서는 안 된다.** 리랭커는 검색 품질을 "개선"하는 보강 단계지 파이프라인의 필수 관문이 아니다.

### 3.2 `doc_store.py` / `pattern_store.py` — 2단계 검색으로 변경

리랭크 이전에는:

```python
results = await get_client().query_points(collection_name=..., query=embedding, limit=top_k or settings.top_k)
```

이제는:

```python
final_k = top_k or settings.top_k
fetch_k = settings.rerank_candidates if settings.rerank_enabled else final_k

results = await get_client().query_points(collection_name=..., query=embedding, limit=fetch_k)
candidates = [...]  # 기존과 동일한 딕셔너리 조립
return rerank(query, candidates, final_k)
```

`fetch_k`가 핵심이다. 리랭크가 켜져 있으면 Qdrant에서 `rerank_candidates`(기본 10)개를 넓게 가져와 재정렬 후 `final_k`(기본 3)개로 좁히고, **꺼져 있으면 `fetch_k == final_k`라서 Qdrant 쿼리 자체가 기능 추가 이전과 완전히 동일하다** — `rerank()`도 `rerank_enabled=False` 분기에서 그대로 `candidates[:top_n]`(= 전체)을 반환하므로, 이 기능을 껐을 때는 코드 경로만 한 겹 더 거칠 뿐 동작·비용·지연 모두 이전과 차이가 없다. `doc_store`와 `pattern_store` 두 곳에 로직을 중복 구현한 이유는, 두 검색이 이미 서로 다른 payload 스키마(`text`+`filename`+`chunk_index` vs `description`+`target_id`+`altitude`+`speed`)로 별도 함수였기 때문 — 공용 헬퍼로 뽑을 만큼 로직이 복잡하지 않아서(4줄) 각자 위치에 그대로 뒀다.

### 3.3 `app/config.py`

```python
cohere_api_key: str = ""
cohere_rerank_model: str = "rerank-v3.5"
rerank_candidates: int = 10

@property
def rerank_enabled(self) -> bool:
    return bool(self.cohere_api_key)
```

`ai_enabled`(Gemini)와 완전히 같은 패턴. `rerank_candidates=10`은 근거가 있는 튜닝값이라기보다 "top_k(3)의 3배 정도면 리랭커가 고를 여지가 충분하면서도 임베딩 검색 자체의 비용은 크게 늘지 않는다"는 상식적 기본값이다 — 실측으로 검증한 숫자는 아니라서, 나중에 RAGAS 점수를 보고 조정할 여지가 있다 (4절).

### 3.4 `/health`에 노출

`app/routers/health.py`, `app/models/schemas.py`에 `rerank_enabled` 필드를 추가했다. `ai_enabled`와 나란히 둬서, 배포 후 "리랭커가 실제로 켜져 있는지"를 `curl /health` 한 번으로 확인할 수 있게 했다 — Secret에 키를 넣고 파드를 재시작했는데 실제로 반영됐는지 헷갈리는 상황(과거 `disablesleep` 확인 오판 사례, `Multipass-Operations-Guide.md` 8절과 비슷한 종류의 함정)을 미리 막기 위함.

## 4. 신경 써야 할 점 / 아직 검증 못 한 것

**① `score` 필드의 의미가 리랭크 on/off에 따라 달라진다.** `doc_store`/`pattern_store`가 조립하는 딕셔너리의 `score`는 원래 Qdrant 코사인 유사도(0~1, distance 기반)였는데, 리랭크가 켜지면 `rerank()`가 이 값을 Cohere `relevance_score`로 **덮어쓴다**. 둘 다 대략 0~1 범위지만 계산 방식이 다른 별개의 지표다. 이 `score`는 `/chat`의 SSE `sources` 이벤트로 프론트엔드에 그대로 노출되므로, 프론트에서 "검색 점수"를 해석하거나 임계값으로 필터링하는 로직을 나중에 추가한다면 **리랭크 on/off에 따라 같은 필드가 다른 의미를 갖는다는 걸 알고 있어야 한다.** 지금 당장 프론트에 그런 로직은 없어서 기능적 문제는 없지만, 문서화는 필요하다고 판단해 여기 남긴다.

**② 실제 API 호출 검증은 못 했다.** Cohere API 키가 없어서, `rerank()`가 실제 응답을 어떻게 파싱하는지는 2절의 스키마 확인(설치 후 시그니처/필드명 검사)으로만 검증했고, 진짜 HTTP 요청-응답 왕복은 아직 안 해봤다. 키를 발급받아 배포한 뒤 최소한 아래를 확인해야 "된다"고 말할 수 있다:

```bash
curl -s http://<host>/health | jq .rerank_enabled   # true인지
# 그 다음 /chat으로 실제 질문을 보내서 sources 순서가 바뀌는지, 에러 없이 도는지 확인
```

**③ 리랭크가 실제로 답변 품질을 개선하는지는 숫자로 아직 못 쟀다.** `eval/evaluate_rag.py`(RAGAS `faithfulness`/`answer_relevancy`)는 `/chat`을 그대로 호출하므로 코드 수정 없이 리랭크 on/off 두 상태로 각각 돌려서 비교할 수 있다 — 이건 실제 키가 들어간 뒤 해야 할 다음 작업으로 남겨둔다. `docs/concepts/11-tool-calling-node-and-ragas-evaluation.md`에서처럼 "됐다고 느껴진다"가 아니라 실측 숫자로 확인하는 게 이 리포의 검증 관례다.

## 5. 배포 쪽 변경 (`k3s-msa-infrastructure` 리포)

`apps/threat-intel-ai-service/deployment.yaml`에 환경변수를 추가했다:

```yaml
- name: COHERE_API_KEY
  valueFrom:
    secretKeyRef:
      name: target-tracking-secrets
      key: cohere-api-key
      optional: true
```

`GEMINI_API_KEY`와 똑같이 기존 `target-tracking-secrets` Secret을 재사용하되, `optional: true`를 붙였다. 이 키가 **아직 실제 클러스터 Secret에는 없기 때문** — `optional`이 없으면 존재하지 않는 키를 참조하는 순간 파드가 `CreateContainerConfigError`로 못 뜬다. 지금 이 상태로 배포해도 리랭커 없이(3절에서 설명한 대로 코드 경로상 동작 동일) 그대로 서비스된다. 실제로 켜려면:

```bash
kubectl -n c4i patch secret target-tracking-secrets \
  --type merge -p '{"stringData":{"cohere-api-key":"<발급받은 키>"}}'
kubectl -n c4i rollout restart deployment/threat-intel-ai-service
curl -s http://<host>/health | jq .rerank_enabled
```

(이 Secret 자체가 GitOps 대상이 아니라 `kubectl`로 직접 관리된다는 건 `docs/Threat-Intel-AI-Service-K3s-Deployment.md` 2.2절에 이미 정리돼 있다 — 이번에도 같은 방식을 따랐다.)

## 6. 관련 문서

- `docs/architecture.md` — 리랭킹/Graceful Degradation 섹션에 요약 반영
- `docs/concepts/03-qdrant-vector-stores.md` — 기존 벡터 검색 구조 (이번 변경의 "1단계")
- `docs/concepts/02-gemini-client-and-graceful-degradation.md` — 먼저 정립된 graceful-degradation 원칙, 이번에 그대로 재사용
- `docs/concepts/11-tool-calling-node-and-ragas-evaluation.md` — RAGAS 평가 스크립트, 리랭크 효과를 실측할 때 그대로 재사용할 도구
- [k3s-msa-infrastructure `docs/Multipass-Operations-Guide.md`](https://github.com/sm010422/k3s-msa-infrastructure/blob/main/docs/Multipass-Operations-Guide.md) — 호스트/워커 리소스 제약(1절 인벤토리)
- [k3s-msa-infrastructure `docs/Threat-Intel-AI-Service-K3s-Deployment.md`](https://github.com/sm010422/k3s-msa-infrastructure/blob/main/docs/Threat-Intel-AI-Service-K3s-Deployment.md) — Secret 관리 방식(GitOps 비대상)
