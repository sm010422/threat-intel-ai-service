# 개념 정리 — 지금 k3s 클러스터에 이 서비스를 얹을 여유가 있나

`kubectl top nodes` / `kubectl describe node`로 실측한 결과 기준. (2026-07-24)

## 클러스터 스펙과 실사용량

| 노드 | CPU | 메모리 capacity | 메모리 실사용 | 메모리 requests / limits | taint |
|---|---|---|---|---|---|
| k3s-master | 2 core | 3027Mi | 1622Mi (54%) | 140Mi / 170Mi | `node-role.kubernetes.io/master:NoSchedule` |
| k3s-worker1 | 1 core | 1487Mi | 712Mi (49%) | **544Mi / 1408Mi (96%)** | 없음 |
| k3s-worker2 | 1 core | 1745Mi | 1027Mi (60%) | 256Mi / 512Mi (30%) | 없음 |

CPU는 세 노드 모두 실사용률이 10% 안팎이라 사실상 병목이 아니다. **메모리가 유일한 제약**이다.

- **master는 후보에서 제외.** control-plane 전용 taint가 걸려 있고, 지금 그 위에서 도는 파드(coredns, traefik, metrics-server 등)는 전부 k3s 애드온이 자체적으로 넣은 toleration 덕에 떠 있는 것 — 우리가 만든 Deployment는 toleration 없이는 애초에 스케줄되지 않고, 굳이 toleration을 추가해서 컨트롤플레인에 올리는 것도 권장되지 않는 방식이라 처음부터 제외했다.
- **worker1은 이미 빡빡하다.** memory limit 합계가 allocatable의 96%. 지금 실사용은 49%로 여유 있어 보이지만, 그건 postgres/redis/target-tracking-service가 각자 limit까지 안 쓰고 있어서일 뿐 — 세 파드가 동시에 limit 근처까지 튀면 이 노드는 바로 포화. 여기에 새 워크로드를 더 얹는 건 피했다.
- **worker2가 실질적인 유일한 후보.** memory request는 겨우 15%만 찬 상태(대부분 argocd 컴포넌트가 request를 안 걸어놔서 그런 것)고, 실사용 기준으로도 718Mi가 비어 있다. 이미 Kafka 브로커가 이 노드에 떠 있어서, 새 서비스의 Kafka consumer가 같은 노드에서 브로커와 통신하면 네트워크 홉도 하나 준다는 부수적 이점도 있다.

## 새 서비스가 실제로 얼마나 먹을까

로컬에서 `docker compose up`으로 직접 띄워서 확인한 실측치(임베딩/생성은 전부 Gemini API 호출이라 **로컬에 LLM/임베딩 모델을 로드하지 않는다** — 이게 메모리 예산을 작게 잡을 수 있는 핵심 이유):

| 컴포넌트 | 예상 request | 예상 limit | 근거 |
|---|---|---|---|
| Qdrant | 128Mi | 384Mi | 공식 이미지 idle 시 100~150Mi대, 벡터 수천 개 규모면 400Mi 안쪽 |
| FastAPI 앱 | 128Mi | 384Mi | uvicorn + langgraph + langchain-text-splitters + qdrant-client + aiokafka. 로컬 모델 없음(torch/numpy 텐서 상주 없음)이라 순수 파이썬 프로세스 기준 가벼운 편 |
| **합계** | **256Mi** | **768Mi** | |

worker2 기준: request 256Mi 추가해도 (256+256)/1745 ≈ 29%, limit 768Mi 추가해도 (512+768)/1745 ≈ 73%. **request 기준으로 스케줄링 가능하고, limit 기준으로도 과포화되지 않는다.**

## 결론

- **된다.** worker2에 배치하는 걸 전제로.
- k8s Deployment/StatefulSet 작성 시 `nodeSelector`나 `podAffinity`로 worker2를 명시하거나, 최소한 worker1을 피하는 `podAntiAffinity`를 걸어두는 걸 권장.
- 실제 배포 후 `kubectl top pods -n <ns>`로 며칠 관찰하면서 limit을 보정하는 게 안전 — 특히 PDF 문서 여러 개를 한 번에 인입하거나 `/chat` 동시 요청이 몰릴 때 FastAPI 프로세스 메모리가 예상보다 튈 수 있음.
- Qdrant는 상태를 갖는 컴포넌트라 PVC가 필요하다 — 클러스터에 이미 `local-path-provisioner`가 떠 있으니 StorageClass는 그대로 재사용 가능.
