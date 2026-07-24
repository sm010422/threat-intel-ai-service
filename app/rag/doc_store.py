import uuid

from qdrant_client.http import models as qmodels

from app.config import settings
from app.llm.gemini_client import embed_text
from app.rag.chunking import chunk_text
from app.rag.qdrant_store import get_client


async def ingest_document(filename: str, text: str) -> tuple[str, int]:
    doc_id = str(uuid.uuid4())
    chunks = chunk_text(text)

    points = []
    for index, chunk in enumerate(chunks):
        embedding = embed_text(chunk, task_type="retrieval_document")
        points.append(
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "doc_id": doc_id,
                    "filename": filename,
                    "chunk_index": index,
                    "text": chunk,
                },
            )
        )

    if points:
        await get_client().upsert(collection_name=settings.qdrant_doc_collection, points=points)

    return doc_id, len(points)


async def search_documents(query: str, top_k: int | None = None) -> list[dict]:
    embedding = embed_text(query, task_type="retrieval_query")
    results = await get_client().query_points(
        collection_name=settings.qdrant_doc_collection,
        query=embedding,
        limit=top_k or settings.top_k,
    )
    return [
        {
            "text": point.payload["text"],
            "score": point.score,
            "metadata": {
                "doc_id": point.payload["doc_id"],
                "filename": point.payload["filename"],
                "chunk_index": point.payload["chunk_index"],
            },
        }
        for point in results.points
    ]
