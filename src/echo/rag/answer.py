"""RAG orchestration: embed the question, retrieve, ground, inject SQL numbers.

Plain English: turn the question into a vector, pull the most semantically
similar feedback out of Postgres, let the model write a grounded answer citing
which items it drew on, then attach a small stats block computed straight from
SQL over that same retrieved set (how many, their sentiment split, dollars at
stake) — the model never touches a figure itself.

Technically: mirrors ``summary/runner.py``'s structured-output + facts-injection
pattern and reuses ``embed.embed_texts`` (question -> vector) and
``money.exposure_for_items`` (dollar aggregate over an arbitrary item set). Every
call writes one ``llm_calls`` audit row (``call_type='rag'``). No new table.
"""

from __future__ import annotations

import time
from collections import Counter

from sqlalchemy import create_engine, insert
from tenacity import retry, stop_after_attempt, wait_exponential

from echo import config
from echo.db import schema
from echo.embed.runner import embed_texts
from echo.money import engine as money
from echo.rag import retrieve
from echo.rag.prompts import RagAnswer, build_messages

_PRICE_IN, _PRICE_OUT = 0.15, 0.60  # gpt-4o-mini USD per 1M tokens
_PRICE_EMBED = 0.02                  # text-embedding-3-small USD per 1M tokens

_LIVE_LLM = None


def _live_llm():
    """Lazily-built structured RAG answerer, reused across calls."""
    global _LIVE_LLM
    if _LIVE_LLM is None:
        from langchain_openai import ChatOpenAI
        _LIVE_LLM = ChatOpenAI(
            model=config.settings.model, temperature=config.RAG_TEMPERATURE, seed=config.SEED,
            api_key=config.settings.openai_api_key,
        ).with_structured_output(RagAnswer, include_raw=True)
    return _LIVE_LLM


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20), reraise=True)
def _invoke(structured_llm, question: str, snippets: list[dict]):
    """Send the question and retrieved snippets to the LLM (retrying on transient failures) and return the parsed answer plus input/output token counts and latency in milliseconds."""
    t0 = time.perf_counter()
    out = structured_llm.invoke(build_messages(question, snippets))
    latency = int((time.perf_counter() - t0) * 1000)
    parsed = out.get("parsed")
    if parsed is None:
        # The model's reply didn't fit the expected answer shape (RagAnswer);
        # raising here lets the @retry decorator above try again instead of
        # silently returning a broken result.
        raise ValueError(f"structured parse failed: {out.get('parsing_error')}")
    raw = out.get("raw")
    um = getattr(raw, "usage_metadata", None) or {}
    return parsed, int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0)), latency


def _stats_over_retrieved(engine, retrieved: list[dict]) -> dict:
    """The one and only source of numbers in the reply — computed over the retrieved set."""
    ids = [r["item_id"] for r in retrieved]
    sentiments = Counter(r["sentiment"] for r in retrieved if r["sentiment"])
    categories = Counter(r["category"] for r in retrieved if r["category"])
    top_category, top_n = categories.most_common(1)[0] if categories else (None, 0)
    m = money.exposure_for_items(ids, engine)
    return {
        "n_retrieved": len(retrieved),
        "sentiment": dict(sentiments),
        "top_category": top_category, "top_category_count": top_n,
        "direct_exposure": m["direct_exposure"], "retention_base": m["retention"]["base"],
        "revenue_at_risk": m["revenue_at_risk"],
    }


def ask(question: str, k: int | None = None, engine=None) -> dict:
    """Answer one question end to end. Returns a plain dict (also the /ask response body).

    Raises ``SystemExit`` if no ``OPENAI_API_KEY`` is configured (mirrors the other
    live stages: classify/embed/summary all require a key the same way).
    """
    if config.settings.use_offline:
        raise SystemExit("rag needs OPENAI_API_KEY (set it in .env).")
    k = k or config.RAG_TOP_K
    eng = engine or create_engine(config.settings.database_url)
    model, pv = config.settings.model, config.RAG_PROMPT_VERSION

    vecs, embed_tokens = embed_texts([question])
    retrieved = retrieve.top_k(eng, vecs[0], k)
    if not retrieved:
        # Nothing to ground an answer on (e.g. embeddings haven't been built yet),
        # so bail out with a plain message instead of asking the LLM to answer
        # from nothing, which would risk it making something up.
        return {"question": question, "answer": "No feedback has been embedded yet — nothing to search.",
                "citations": [], "stats": None, "model": model, "prompt_version": pv}

    parsed, in_t, out_t, lat = _invoke(_live_llm(), question, retrieved)

    by_id = {r["item_id"]: r for r in retrieved}
    cited = [cid for cid in parsed.cited_item_ids if cid in by_id]  # drop any hallucinated id
    citations = [{"item_id": cid, "source_type": by_id[cid]["source_type"],
                  "snippet": by_id[cid]["text"][:200]} for cid in cited]
    stats = _stats_over_retrieved(eng, retrieved)

    # Log this call to the llm_calls audit table (same pattern every LLM stage
    # follows) so token usage, latency, and cost can be tracked over time.
    with eng.begin() as c:
        c.execute(insert(schema.llm_calls), {
            "item_id": None, "call_type": "rag", "model_name": model, "prompt_version": pv,
            "input": question[:2000], "output": parsed.answer[:2000],
            "prompt_tokens": in_t or None, "completion_tokens": out_t or None,
            "total_tokens": (in_t + out_t) or None, "latency_ms": lat or None,
            "status": "ok", "error": None})

    cost = in_t / 1e6 * _PRICE_IN + out_t / 1e6 * _PRICE_OUT + embed_tokens / 1e6 * _PRICE_EMBED
    return {"question": question, "answer": parsed.answer, "citations": citations, "stats": stats,
            "model": model, "prompt_version": pv,
            "tokens_in": in_t, "tokens_out": out_t, "est_cost": round(cost, 4)}
