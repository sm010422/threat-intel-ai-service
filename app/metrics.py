"""Prometheus 계측. /metrics에서 scrape 가능한 형태로 노출한다.

무거운 옵저버빌리티 스택(Prometheus 서버, Grafana)을 클러스터에 새로 올리는 대신
-- k3s-msa-infrastructure/docs/K3s-Node-Resource-Planning-Troubleshooting.md에
기록된 대로 worker 노드가 이미 메모리 70%대/부하 1.0 이상이라 새 스테이트풀
워크로드를 얹을 여유가 없다 -- 앱 쪽만 Prometheus 포맷으로 계측해서 /metrics로
노출해둔다. 실제 스크레이핑은 나중에 여유가 생기면 붙이면 된다.
"""

from prometheus_client import Counter, Histogram

chat_route_total = Counter(
    "threat_ai_chat_route_total",
    "라우팅된 /chat 질문 수",
    ["route"],
)

tool_call_total = Counter(
    "threat_ai_tool_call_total",
    "Gemini function-calling으로 실제 호출된 도구 수",
    ["tool_name"],
)

retrieval_latency_seconds = Histogram(
    "threat_ai_retrieval_latency_seconds",
    "classify + RAG 검색(LangGraph) 소요 시간 -- LLM 스트리밍 생성 시간은 미포함",
    ["route"],
)
