from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import settings

_client: AsyncQdrantClient | None = None


def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    return _client


async def ensure_collections() -> None:
    client = get_client()
    for collection in (settings.qdrant_doc_collection, settings.qdrant_pattern_collection):
        exists = await client.collection_exists(collection)
        if not exists:
            await client.create_collection(
                collection_name=collection,
                vectors_config=qmodels.VectorParams(
                    size=settings.embedding_dimension,
                    distance=qmodels.Distance.COSINE,
                ),
            )


async def is_connected() -> bool:
    try:
        await get_client().get_collections()
        return True
    except Exception:
        return False
