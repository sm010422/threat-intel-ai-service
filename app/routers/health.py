from fastapi import APIRouter, Response

from app.config import settings
from app.kafka import consumer as kafka_consumer
from app.models.schemas import HealthResponse
from app.rag.qdrant_store import is_connected

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        ai_enabled=settings.ai_enabled,
        rerank_enabled=settings.rerank_enabled,
        qdrant_connected=await is_connected(),
        kafka_consumer_running=kafka_consumer.is_running,
    )


@router.get("/live", include_in_schema=False)
async def liveness() -> Response:
    """k8s livenessProbe 전용. /health는 상태와 무관하게 항상 200을 반환해서
    2026-09-27 사건(Kafka consumer가 재시도 없이 죽어 kafka_consumer_running=false로
    몇 시간이고 방치됨, docs/Chuseok-Power-Outage-Recovery.md)을 잡아내지 못했다.
    consumer.py에 재시도 로직을 넣어 근본 원인은 고쳤지만, 다른 이유로 컨슈머가
    다시 죽어있는 상태가 되는 미래의 버그까지 방어하기 위해 이 필드를 실제로 파드
    재시작 트리거에 반영한다. kafka_enabled=False면 애초에 컨슈머가 안 뜨는 게
    정상이므로 검사 대상에서 뺀다.
    """
    if settings.kafka_enabled and not kafka_consumer.is_running:
        return Response(status_code=503, content="kafka consumer not running")
    return Response(status_code=200)
