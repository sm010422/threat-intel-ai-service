from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str


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
