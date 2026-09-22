import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

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

# 데모/로컬 개발 편의를 위해 전체 오리진 허용 (portfolio 프로젝트, 인증 붙는 순간 좁혀야 함).
# 프로덕션 경로(같은 Traefik Ingress 아래 /ai prefix)는 same-origin이라 애초에 CORS를 안 탄다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(chat.router, tags=["chat"])
app.include_router(ingest.router, tags=["ingest"])


@app.get("/metrics", include_in_schema=False)
@app.get("/ai/metrics", include_in_schema=False)
def metrics() -> Response:
    # app.mount(make_asgi_app())로 했더니 trailing slash 없는 요청(/ai/metrics)이
    # Starlette Mount의 기본 redirect_slashes 동작 때문에 http://로 307 리다이렉트되는
    # 문제가 있었다 (Traefik이 TLS를 종단하는 구조라 실제로 https 뒤에서 http Location
    # 헤더가 나가는 상황). 다른 라우터들과 똑같이 일반 route로 바꿔서 회피.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# target-tracking-service 대시보드가 같은 Traefik Ingress 아래 /ai prefix로 이 서비스를
# 호출할 수 있도록, 동일한 라우터를 /ai prefix로 한 번 더 등록한다 (루트 경로는 하위 호환
# 유지 -- 기존 문서/curl 예제가 전부 루트 경로 기준이라 그대로 둠).
app.include_router(health.router, prefix="/ai", tags=["health"], include_in_schema=False)
app.include_router(chat.router, prefix="/ai", tags=["chat"], include_in_schema=False)
app.include_router(ingest.router, prefix="/ai", tags=["ingest"], include_in_schema=False)
