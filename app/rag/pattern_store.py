import uuid
from datetime import UTC, datetime

from qdrant_client.http import models as qmodels

from app.config import settings
from app.llm.gemini_client import embed_text
from app.models.schemas import TargetEvent
from app.rag.qdrant_store import get_client


def describe_event(event: TargetEvent) -> str:
    return (
        f"표적ID={event.targetId}, 유형={event.targetType}, "
        f"위도={event.latitude}, 경도={event.longitude}, "
        f"고도={event.altitude}m, 속도={event.speed}km/h, 상태={event.status}"
    )


async def upsert_target_event(event: TargetEvent) -> None:
    description = describe_event(event)
    embedding = embed_text(description, task_type="retrieval_document")

    point = qmodels.PointStruct(
        id=str(uuid.uuid4()),
        vector=embedding,
        payload={
            "target_id": event.targetId,
            "target_type": event.targetType,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "altitude": event.altitude,
            "speed": event.speed,
            "status": event.status,
            "description": description,
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
    await get_client().upsert(collection_name=settings.qdrant_pattern_collection, points=[point])


async def search_patterns(query: str, top_k: int | None = None) -> list[dict]:
    embedding = embed_text(query, task_type="retrieval_query")
    results = await get_client().query_points(
        collection_name=settings.qdrant_pattern_collection,
        query=embedding,
        limit=top_k or settings.top_k,
    )
    return [
        {
            "text": point.payload["description"],
            "score": point.score,
            "metadata": {
                "target_id": point.payload["target_id"],
                "target_type": point.payload["target_type"],
                "altitude": point.payload["altitude"],
                "speed": point.payload["speed"],
                "observed_at": point.payload["observed_at"],
            },
        }
        for point in results.points
    ]
