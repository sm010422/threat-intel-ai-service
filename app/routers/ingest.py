import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from pypdf import PdfReader

from app.config import settings
from app.models.schemas import IngestDocResponse
from app.rag.doc_store import ingest_document

router = APIRouter()


@router.post("/ingest/doc", response_model=IngestDocResponse)
async def ingest_doc(file: UploadFile = File(...)) -> IngestDocResponse:
    if not settings.ai_enabled:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

    raw = await file.read()
    filename = file.filename or "untitled.txt"

    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        text = raw.decode("utf-8", errors="ignore")

    if not text.strip():
        raise HTTPException(status_code=400, detail="No extractable text in document")

    doc_id, chunk_count = await ingest_document(filename, text)
    return IngestDocResponse(doc_id=doc_id, chunk_count=chunk_count)
