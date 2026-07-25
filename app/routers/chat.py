import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.config import settings
from app.graph.router_graph import classify_and_retrieve
from app.llm.gemini_client import generate_stream
from app.models.schemas import ChatRequest

router = APIRouter()


def _build_prompt(route: str, question: str, context: str) -> str:
    if route == "pattern_search":
        instruction = (
            "아래는 과거 탐지 이력 중 질문과 유사한 항목들이다. "
            "이를 근거로 질문에 답하고, 유사 패턴이 반복되는 추세인지 언급하라."
        )
    else:
        instruction = (
            "아래는 위협 인텔리전스 문서에서 검색된 관련 내용이다. "
            "이를 근거로 질문에 답하라. 문서에 없는 내용은 추측하지 말고 모른다고 답하라."
        )
    return f"{instruction}\n\n[검색된 컨텍스트]\n{context}\n\n[질문]\n{question}\n\n[답변]"


async def _stream_chat(question: str) -> AsyncGenerator[str, None]:
    result = await classify_and_retrieve(question)
    route = result["route"]
    sources = result["sources"]
    prompt = _build_prompt(route, question, result["context"])

    queue: asyncio.Queue[str | BaseException | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def produce() -> None:
        try:
            for token in generate_stream(prompt):
                loop.call_soon_threadsafe(queue.put_nowait, token)
        except Exception as exc:  # noqa: BLE001 - surfaced to the client below, not swallowed
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, produce)

    yield f"event: route\ndata: {json.dumps({'route': route})}\n\n"

    while True:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, BaseException):
            yield f"event: error\ndata: {json.dumps({'message': str(item)})}\n\n"
            break
        yield f"data: {json.dumps({'token': item})}\n\n"

    yield f"event: sources\ndata: {json.dumps(sources)}\n\n"
    yield "event: done\ndata: {}\n\n"


async def _stream_disabled() -> AsyncGenerator[str, None]:
    yield f"event: error\ndata: {json.dumps({'message': 'GEMINI_API_KEY not configured'})}\n\n"


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    if not settings.ai_enabled:
        return StreamingResponse(_stream_disabled(), media_type="text/event-stream")
    return StreamingResponse(_stream_chat(request.question), media_type="text/event-stream")
