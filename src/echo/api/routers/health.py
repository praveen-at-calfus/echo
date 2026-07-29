"""``GET /health`` — DB + LLM availability + build id (drives UI degradation)."""

from __future__ import annotations

from fastapi import APIRouter

from echo.api import deps

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict:
    """GET /health: report whether the database is reachable and whether live LLM features (ask/submit) can run; the frontend uses this to show a status banner and enable/disable features."""
    ok = deps.db_ok()
    return {"status": "ok" if ok else "degraded",
            "db": ok, "llm": deps.llm_available(), "build_id": deps.BUILD_ID}
