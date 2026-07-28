"""The echo FastAPI app — the single backend the dashboard talks to.

Plain English: exposes everything the pipeline computed (stats, themes, urgent
queue, weekly summary, feedback) as JSON, plus a live-submit endpoint. The LLM
still only classifies/narrates; every figure here is SQL-computed.

Run: ``uvicorn echo.api.main:app`` (or ``python -m echo.api``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from echo.api import deps
from echo.api.routers import ask, feedback, health, stats, summary, themes, urgent
from echo.api.routers import eval as eval_router
from echo.db import schema, vector


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent, never truncates — a safety net so the API works even against
    # a bare Postgres (no seed dump restored yet), same create_all every batch
    # stage already calls at the start of its own run().
    engine = deps.get_engine()
    # vector.create_all() enables the pgvector extension and creates `embeddings`
    # first — it shares schema.metadata, so the extension must exist before the
    # unscoped create_all below (which would otherwise try to build that table
    # too, without the `vector` type registered yet).
    vector.create_all(engine)
    schema.metadata.create_all(engine)
    yield


app = FastAPI(
    title="echo API",
    version=deps.BUILD_ID,
    description="AI customer-feedback intelligence — classify, money, themes, weekly summary, ask echo.",
    lifespan=lifespan,
)

# The Streamlit frontend is a separate origin; allow browser calls.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

for r in (health.router, stats.router, themes.router, urgent.router, summary.router,
          feedback.router, ask.router, eval_router.router):
    app.include_router(r)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {"service": "echo", "build_id": deps.BUILD_ID, "docs": "/docs"}
