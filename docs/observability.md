# 관측성 (Prometheus 계측 + RAGAS 회귀 게이트)

## 요약: 지금 뭐가 떠 있고 뭐가 안 떠 있나

**Prometheus 서버는 클러스터에 안 떠 있다.** 한 게 정확히는:

1. 앱(`threat-intel-ai-service`)에 `prometheus-client` 라이브러리를 추가
2. `GET /metrics` (외부 공개 경로는 `GET /ai/metrics`) 라우트를 하나 직접 만들어서, 요청이 올 때마다 그 순간까지 앱 안에 누적된 지표(Counter/Histogram 값)를 Prometheus 텍스트 포맷으로 즉석에서 렌더링해서 응답
3. 이 엔드포인트를 실제로 주기적으로 긁어가서(scrape) 시계열로 저장·쿼리·그래프화하는 건 원래 Prometheus 서버(+Grafana)가 하는 일인데 **그 서버 자체를 새로 만들지 않았다**

비유하면: "체온계는 몸에 붙여놨는데, 30분마다 값을 기록해서 그래프로 보여주는 간호사(Prometheus)는 아직 안 고용한" 상태다. `curl /ai/metrics`로 지금 이 순간의 누적 숫자는 언제든 볼 수 있지만, "어제보다 latency가 늘었나?" 같은 추세는 못 본다 — 누군가(Prometheus)가 그 값을 주기적으로 수집해서 쌓아둬야 하는데 지금은 안 쌓인다.

**왜 이렇게 했는지는 아래 "왜 풀 스택을 새로 안 올렸나" 참고.**

## 왜 풀 스택(Prometheus 서버 + Grafana)을 새로 안 올렸나

클러스터 실측 결과(2026-09-16, `k3s-msa-infrastructure` 관련 대화) worker 노드가 이미 메모리 70%대, load average가 vCPU 수를 넘는 상태였다. 여기에 Prometheus(TSDB, 지속적인 디스크/메모리 사용) + Grafana를 새 파드로 얹는 건 리소스 압박을 더 키우는 선택이라 보류했다.

대신 **앱이 Prometheus 포맷으로 스스로를 계측**하는 데까지만 하고, 실제 스크레이핑 서버는 나중에 여유가 생기면(또는 임시로 `kubectl port-forward` + 로컬 Prometheus로) 붙이는 걸로 남겨둔다. `/metrics`를 curl 한 번으로 보는 것만으로도 "이 서비스가 뭘 계측하는지" 보여주기엔 충분하다.

## 노출되는 지표 (`GET /metrics`, Prometheus 텍스트 포맷)

| 지표 | 타입 | 라벨 | 의미 |
|---|---|---|---|
| `threat_ai_chat_route_total` | Counter | `route` (doc_rag / pattern_search) | `/chat` 질문이 어느 경로로 라우팅됐는지 |
| `threat_ai_tool_call_total` | Counter | `tool_name` | Gemini function-calling으로 실제 호출된 도구 |
| `threat_ai_retrieval_latency_seconds` | Histogram | `route` | classify + RAG 검색(LangGraph) 소요 시간. LLM 스트리밍 생성 시간은 별도(스트림이라 사후 집계가 애매해서 이번엔 제외) |

```bash
curl https://k3s-master.taildcdcee.ts.net/ai/metrics   # 공개 Ingress (/ai prefix)를 통한 접근
curl http://localhost:8000/metrics                     # 로컬/클러스터 내부에서 루트 경로로 접근
```

## RAGAS 회귀 평가를 CI에 올리되, 자동 트리거는 안 함

`eval/evaluate_rag.py`는 원래 로컬에서 수동으로 돌리는 스크립트였다. 이번에 두 가지를 더했다.

1. `--fail-below <threshold>` 옵션 — faithfulness/answer_relevancy 평균이 기준 미만이면 non-zero exit
2. `.github/workflows/eval.yml` — 위 스크립트를 CI에서 실행

**단, `on: push`/`on: pull_request`로 자동 실행하지 않고 `workflow_dispatch`(수동 트리거)로만 뒀다.** Gemini 무료 tier 일일 한도가 낮아서(`docs/concepts/11-tool-calling-node-and-ragas-evaluation.md` 참고) 매 PR/push마다 자동으로 돌리면:

- RAGAS 자체가 judge LLM 호출을 질문당 여러 번 하고(현재 3개 질문 × faithfulness/answer_relevancy 2개 지표 = 6회 이상)
- 대시보드에서 사람이 수동으로 쓰는 쿼터와 같은 키를 공유해서 충돌

이 프로젝트가 이미 여러 곳(`ai.auto-analysis.enabled` 기본 false, `ANALYSIS_COOLDOWN` 30분 등)에서 같은 이유로 자동 호출을 의도적으로 막아둔 것과 동일한 판단이다. "PR마다 자동 회귀 테스트"가 이상적이지만, 무료 tier 쿼터라는 현실적 제약 안에서는 "품질 게이트가 필요한 시점에 사람이 명시적으로 트리거"하는 쪽이 더 실용적이다.

### 실행 방법

```bash
gh workflow run eval.yml \
  -f base_url=https://k3s-master.taildcdcee.ts.net/ai \
  -f fail_below=0.5
```

GitHub Actions 시크릿에 `GEMINI_API_KEY`가 등록돼 있어야 한다 (judge 모델 호출용, target-tracking-secrets와 같은 키 재사용 가능). 저장소에 아직 없다면:

```bash
gh secret set GEMINI_API_KEY
```
