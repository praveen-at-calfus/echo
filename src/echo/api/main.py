"""The echo FastAPI app — the single backend the dashboard talks to.

Plain English: exposes everything the pipeline computed (stats, themes, urgent
queue, weekly summary, feedback) as JSON, plus a live-submit endpoint. The LLM
still only classifies/narrates; every figure here is SQL-computed.

Run: ``uvicorn echo.api.main:app`` (or ``python -m echo.api``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from echo.api import deps
from echo.api.routers import ask, auth, feedback, health, stats, summary, themes, urgent
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
    # `feedback.submitter_id` was added after the existing DB + Docker seed dump
    # were created; create_all only creates missing *tables*, never alters an
    # extant one, so add the column idempotently here (Postgres IF NOT EXISTS).
    with engine.begin() as c:
        c.execute(text(
            "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS "
            "submitter_id VARCHAR REFERENCES users(id)"
        ))
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

# Public routers: no auth (health powers the frontend badge + Docker healthcheck;
# auth is the login desk itself).
app.include_router(health.router)
app.include_router(auth.router)

# Company-only: analytics + reads. The role guard is attached at include time, so
# a GEN-POP token gets a 403 here even when driving these from Swagger UI — the
# security boundary is server-side, not UI-hidden. Zero edits to these routers.
for r in (stats.router, themes.router, urgent.router, summary.router,
          ask.router, eval_router.router):
    app.include_router(r, dependencies=[Depends(deps.require_company)])

# Mixed access — guards live per-endpoint inside the router (POST: any logged-in
# user; GET: gen_pop sees only their own, company sees all).
app.include_router(feedback.router)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {"service": "echo", "build_id": deps.BUILD_ID, "docs": "/docs"}
