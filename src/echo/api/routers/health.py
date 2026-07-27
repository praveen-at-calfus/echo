"""``GET /health`` — DB + LLM availability + build id (drives UI degradation)."""

from __future__ import annotations

from fastapi import APIRouter

from echo.api import deps

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict:
    ok = deps.db_ok()
    return {"status": "ok" if ok else "degraded",
            "db": ok, "llm": deps.llm_available(), "build_id": deps.BUILD_ID}
