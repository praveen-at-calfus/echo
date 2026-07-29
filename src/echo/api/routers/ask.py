"""``POST /ask`` — "ask echo": grounded, cited Q&A over the feedback corpus (RAG, bonus).

Gated on ``deps.llm_available()`` like live /feedback: retrieval + generation
both need an OpenAI key. Reuses ``rag.answer.ask`` end to end (embed the
question, pgvector top-k, grounded answer, SQL-injected numbers).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from echo.api import deps
from echo.api.schemas import AskIn

router = APIRouter(tags=["ask"])


@router.post("/ask")
def ask(body: AskIn) -> dict:
    """POST /ask: answer a free-text question about the feedback corpus using retrieval-augmented generation, returning a grounded, cited answer with SQL-computed stats."""
    # This feature needs to call an LLM (both to search and to write the
    # answer), so block the request up front if no API key is configured
    # rather than failing partway through.
    if not deps.llm_available():
        raise HTTPException(503, "ask echo needs an OpenAI key (set OPENAI_API_KEY)")
    from echo.rag.answer import ask as run_ask
    return run_ask(body.question, k=body.k, engine=deps.get_engine())
