from typing import Literal

from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str


class SourceChunk(BaseModel):
    text: str
    score: float
    metadata: dict


class ChatRouteResult(BaseModel):
    route: Literal["doc_rag", "pattern_search"]
    context: str
    sources: list[SourceChunk]


class IngestDocResponse(BaseModel):
    doc_id: str
    chunk_count: int


class HealthResponse(BaseModel):
    status: str
    ai_enabled: bool
    qdrant_connected: bool
    kafka_consumer_running: bool


class TargetEvent(BaseModel):
    targetId: str
    targetType: str
    latitude: float
    longitude: float
    altitude: float
    speed: float
    status: str
