"""RAG stage — "ask echo": embed a question, pgvector cosine top-k retrieval,
a grounded and cited LLM answer, with every number injected from a separate SQL
aggregate over the retrieved set (the model never emits a figure).

See :mod:`echo.rag.retrieve` for the pgvector query, :mod:`echo.rag.prompts` for
the grounding contract, and :mod:`echo.rag.answer` for the orchestration.
"""

from __future__ import annotations

from echo.rag.answer import ask

__all__ = ["ask"]
