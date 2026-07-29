"""``/feedback`` — list analyzed feedback (GET) and submit one live (POST).

GET filters + paginates the stored analysis. POST runs the real-time pipeline on
a new item: insert the immutable feedback row, classify (one LLM call + urgency
floor), embed, attach money — reusing the same functions the batch stages use.
The text is never mutated; analysis is written versioned like the batch path.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from langdetect import DetectorFactory, LangDetectException, detect
from sqlalchemy import insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from echo import config
from echo.api import deps
from echo.api.schemas import FeedbackIn
from echo.db import schema, vector

DetectorFactory.seed = 0  # deterministic detection, matching the project's SEED-everywhere convention

router = APIRouter(tags=["feedback"])


def _detect_language(text: str) -> str | None:
    """Best-effort ISO 639-1 code for live-submitted text (unlike the batch corpus,
    which is Portuguese by construction, a live submission can be anything).
    None on genuine detection failure (very short/ambiguous text) rather than
    guessing — matches the project's "never fabricate a value" discipline."""
    try:
        return detect(text)
    except LangDetectException:
        return None


@router.get("/feedback")
def list_feedback(
    category: str | None = None,
    sentiment: str | None = Query(None, pattern="^(positive|neutral|negative)$"),
    source_type: str | None = Query(None, pattern="^(review|ticket|survey)$"),
    min_urgency: int | None = Query(None, ge=1, le=5),
    q: str | None = Query(None, description="case-insensitive substring of the text"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(deps.get_current_user),
) -> dict:
    """GET /feedback: list stored feedback items with optional filters (category, sentiment, source, minimum urgency, text search) and pagination; gen_pop users only see their own submissions, company users see everything."""
    where = ["a.model_name = :model", "a.prompt_version = :pv"]
    p: dict = {"model": deps.MODEL, "pv": deps.PROMPT_VERSION, "limit": limit, "offset": offset}
    optional = [("a.category = :category", "category", category),
                ("a.sentiment = :sentiment", "sentiment", sentiment),
                ("f.source_type = :source_type", "source_type", source_type),
                ("a.urgency >= :min_urgency", "min_urgency", min_urgency),
                ("f.text ILIKE :q", "q", f"%{q}%" if q else None)]
    for cond, key, val in optional:
        if val is not None:
            where.append(cond)
            p[key] = val
    # GEN-POP users only ever see the feedback they submitted; COMPANY sees all.
    if user["role"] == config.ROLE_GEN_POP:
        where.append("f.submitter_id = :uid")
        p["uid"] = user["id"]
    clause = " AND ".join(where)

    with deps.get_engine().connect() as c:
        total = c.execute(text(f"SELECT count(*) FROM analysis a JOIN feedback f "
                              f"ON f.item_id=a.item_id WHERE {clause}"), p).scalar()
        rows = c.execute(text(f"""
            SELECT f.item_id, f.source_type, a.category, a.sentiment, a.urgency, a.rationale,
                   a.source_score_disagreement, f.order_value, left(f.text, 400) AS text,
                   f.created_at::text AS created_at
            FROM analysis a JOIN feedback f ON f.item_id = a.item_id
            WHERE {clause} ORDER BY a.urgency DESC, f.order_value DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        """), p).mappings().all()
    return {"total": int(total), "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}


@router.post("/feedback", status_code=201)
def create_feedback(item: FeedbackIn, user: dict = Depends(deps.get_current_user)) -> dict:
    """POST /feedback: submit one new feedback item live, running it through the same classify + embed + money pipeline the batch corpus uses, and store the result tied to the submitting user."""
    if not deps.llm_available():
        raise HTTPException(503, "live submission needs an OpenAI key (set OPENAI_API_KEY)")
    from echo.classify.crosscheck import disagreement
    from echo.classify.runner import _analysis_hash, classify_text
    from echo.embed.runner import embed_texts

    item_id = str(uuid.uuid4())
    now = datetime.now()
    eng = deps.get_engine()

    # Run the same LLM classification (category/sentiment/urgency) the batch
    # pipeline uses, so live submissions are scored identically to the corpus.
    analysis, usage = classify_text(item.text)
    # Also turn the text into a semantic vector (an "embedding") so this new
    # item can be found later by theme clustering and by "ask echo" search.
    vecs, _tokens = embed_texts([item.text])
    # Flag it if the model's sentiment call disagrees with the star rating or
    # NPS score the customer actually gave, if they gave one.
    dis = disagreement(analysis["sentiment"], item.source_score, item.source_scale)
    a_hash = _analysis_hash(item.text, deps.PROMPT_VERSION, deps.MODEL)

    with eng.begin() as c:
        c.execute(insert(schema.feedback), {
            "item_id": item_id, "source_type": item.source_type, "text": item.text,
            "source_score": item.source_score, "source_scale": item.source_scale,
            "order_value": item.order_value, "refund_amount": item.refund_amount,
            "fulfillment_outcome": item.fulfillment_outcome,
            "created_at": now, "language": _detect_language(item.text), "synthetic": False,
            "submitter_id": user["id"]})
        c.execute(insert(schema.analysis), {
            "item_id": item_id, "category": analysis["category"], "sentiment": analysis["sentiment"],
            "urgency": analysis["urgency"], "rationale": analysis["rationale"],
            "source_score_disagreement": dis, "model_name": deps.MODEL,
            "prompt_version": deps.PROMPT_VERSION, "analysis_hash": a_hash})
        c.execute(pg_insert(vector.embeddings).on_conflict_do_nothing(index_elements=[vector.embeddings.c.item_id]),
                  {"item_id": item_id, "model": config.EMBED_MODEL, "embedding": vecs[0]})
        c.execute(insert(schema.llm_calls), {
            "item_id": item_id, "call_type": "classify", "model_name": deps.MODEL,
            "prompt_version": deps.PROMPT_VERSION, "input": item.text[:2000],
            "prompt_tokens": usage["input_tokens"] or None, "completion_tokens": usage["output_tokens"] or None,
            "latency_ms": usage["latency_ms"] or None, "status": "ok"})

    from echo.money import engine as money
    # Compute the dollar amount at stake for this one item using the same
    # money engine the batch pipeline uses, so live and batch figures line up.
    exposure = money.exposure_for_items([item_id], eng)
    return {"item_id": item_id, "analysis": analysis,
            "source_score_disagreement": dis, "money": exposure}
