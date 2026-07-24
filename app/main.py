import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.kafka.consumer import run_consumer
from app.rag.qdrant_store import ensure_collections
from app.routers import chat, health, ingest

logging.basicConfig(level=logging.INFO)

_kafka_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kafka_task
    await ensure_collections()
    if settings.kafka_enabled:
        _kafka_task = asyncio.create_task(run_consumer())
    yield
    if _kafka_task:
        _kafka_task.cancel()


app = FastAPI(title="threat-intel-ai-service", lifespan=lifespan)

app.include_router(health.router, tags=["health"])
app.include_router(chat.router, tags=["chat"])
app.include_router(ingest.router, tags=["ingest"])
