from fastapi import APIRouter

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
        qdrant_connected=await is_connected(),
        kafka_consumer_running=kafka_consumer.is_running,
    )
