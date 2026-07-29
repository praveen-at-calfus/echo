"""Batch classify engine.

Plain English: pull the unclassified feedback from Postgres, ask the model to
label each (concurrently), force the urgency floor, flag score/sentiment
disagreements, and write the results to ``analysis`` + an audit trail to
``llm_calls``. Identical texts are labeled once and reused (a content-hash
cache), so duplicates and re-runs are free.

Technically: mirrors the corpus builder's LLM pattern (structured output +
tenacity retry + a ThreadPoolExecutor over unique texts) and its SQLAlchemy Core
bulk-insert pattern. Idempotent: only items with no analysis row for the current
(model, prompt_version) are processed, and inserts use ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import and_, create_engine, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from echo import config
from echo.classify.crosscheck import disagreement
from echo.classify.prompts import build_messages
from echo.classify.urgency_floor import apply_floor
from echo.corpus.utils import normalize_text
from echo.db import schema
from echo.schemas.analysis import Classification

_PRICE_IN, _PRICE_OUT = 0.15, 0.60  # gpt-4o-mini USD per 1M tokens


def _analysis_hash(text: str, prompt_version: str, model: str) -> str:
    """Build a cache key from the normalized text plus prompt version and model, so identical texts reuse one result."""
    return hashlib.sha256(f"{normalize_text(text)}|{prompt_version}|{model}".encode()).hexdigest()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20), reraise=True)
def _invoke(structured_llm, text: str):
    """Send one text to the model and return (parsed classification, input tokens, output tokens, total tokens, latency ms, raw reply text), retrying up to 3 times on transient failures."""
    t0 = time.perf_counter()
    out = structured_llm.invoke(build_messages(text))
    latency = int((time.perf_counter() - t0) * 1000)
    parsed = out.get("parsed")
    if parsed is None:
        # The model's reply didn't match our expected Classification shape; treat it as a
        # failure so the retry decorator (or the caller) can handle it instead of silently
        # passing along a broken result.
        raise ValueError(f"structured parse failed: {out.get('parsing_error')}")
    raw = out.get("raw")
    # usage_metadata holds how many tokens the call cost, used later to estimate spend.
    um = getattr(raw, "usage_metadata", None) or {}
    return (parsed, int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0)),
            int(um.get("total_tokens", 0)), latency, (getattr(raw, "content", "") or ""))


_LIVE_LLM = None


def _live_llm():
    """Lazily-built structured classifier reused across live (API) calls."""
    global _LIVE_LLM
    if _LIVE_LLM is None:
        from langchain_openai import ChatOpenAI
        _LIVE_LLM = ChatOpenAI(
            model=config.settings.model, temperature=config.CLASSIFY_TEMPERATURE, seed=config.SEED,
            api_key=config.settings.openai_api_key,
        ).with_structured_output(Classification, include_raw=True)
    return _LIVE_LLM


def classify_text(text: str, floor_signal: bool | None = None) -> tuple[dict, dict]:
    """Classify one item live (single LLM call + urgency floor). No DB writes.

    Returns (analysis_dict, usage) — analysis has category/sentiment/urgency/rationale/floored.
    """
    parsed, in_t, out_t, tot_t, lat, raw = _invoke(_live_llm(), text)
    urg, floored = apply_floor(parsed.urgency, text, floor_signal)
    analysis = {"category": parsed.category, "sentiment": parsed.sentiment, "urgency": urg,
                "rationale": parsed.rationale, "floored": floored}
    return analysis, {"input_tokens": in_t, "output_tokens": out_t, "latency_ms": lat, "raw": raw}


def _score_only_sentiment(score, scale):
    """Turn a numeric survey score into (sentiment, urgency) without calling the model, for items that have no free text."""
    if scale == "nps_0_10":
        # NPS (Net Promoter Score) is a 0-10 "how likely to recommend us" question.
        # Low scores are unhappy customers (moderate urgency), high scores are happy ones
        # (low urgency); everything in between is treated as neutral, average urgency.
        if score is not None and score <= config.NPS_NEG_MAX:
            return "negative", 3
        if score is not None and score >= config.NPS_POS_MIN:
            return "positive", 1
    return "neutral", 2


def run_score_only(engine, model: str, pv: str, limit: int | None = None) -> dict:
    """Score-only surveys (NPS, no free text): derive sentiment/urgency from the
    score — no LLM. Category is ``Other/Unclear`` (no text → no identifiable topic;
    inventing one would break the anti-hallucination invariant). Idempotent for the
    current (model, prompt_version); audited in ``llm_calls`` as ``score_derived``.
    """
    a, f = schema.analysis, schema.feedback
    # Left-join feedback to analysis for this exact (model, prompt_version): a NULL on the
    # analysis side means "not analysed yet for this version", which is how we find work
    # to do without re-processing items we've already handled.
    j = f.outerjoin(a, and_(a.c.item_id == f.c.item_id,
                            a.c.model_name == model, a.c.prompt_version == pv))
    q = (select(f.c.item_id, f.c.source_score, f.c.source_scale)
         .select_from(j)
         .where(and_(func.length(func.trim(f.c.text)) == 0, a.c.item_id.is_(None)))
         .order_by(f.c.item_id))
    if limit:
        q = q.limit(limit)
    with engine.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(q)]
    if not rows:
        return {"score_only": 0}

    analysis_rows, llm_rows = [], []
    cats = Counter()
    for it in rows:
        sent, urg = _score_only_sentiment(it["source_score"], it["source_scale"])
        rat = (f"Score-only NPS survey (no free text): sentiment and urgency "
               f"derived from the score {it['source_score']}/10.")
        cats[sent] += 1
        analysis_rows.append({"item_id": it["item_id"], "category": "Other/Unclear",
                              "sentiment": sent, "urgency": urg, "rationale": rat,
                              "confidence": None, "source_score_disagreement": None,
                              "model_name": model, "prompt_version": pv, "analysis_hash": None})
        llm_rows.append({"item_id": it["item_id"], "call_type": "score_derived",
                         "model_name": model, "prompt_version": pv, "input": None,
                         "output": None, "status": "ok", "error": None})

    with engine.begin() as c:
        # ON CONFLICT DO NOTHING makes this insert safe to re-run: if a row for this
        # (item, model, prompt_version) already exists, the duplicate insert is just skipped.
        stmt = pg_insert(a).on_conflict_do_nothing(constraint="uq_analysis_item_version")
        c.execute(stmt, analysis_rows)
        c.execute(insert(schema.llm_calls), llm_rows)
    print(f"score-only surveys: {len(analysis_rows)} rows written (no LLM) · sentiment {dict(cats)}")
    return {"score_only": len(analysis_rows), "sentiment": dict(cats)}


def run(limit: int | None = None, workers: int | None = None) -> dict:
    """Run the full classify stage end to end (score-only surveys, then LLM classification of texted items, then writing results and printing a summary); returns a dict of run stats."""
    workers = workers or config.CLASSIFY_WORKERS
    model = config.settings.model
    pv = config.CLASSIFY_PROMPT_VERSION
    if config.settings.use_offline:
        raise SystemExit("classify needs OPENAI_API_KEY (set it in .env).")

    engine = create_engine(config.settings.database_url)
    schema.metadata.create_all(engine)
    a, f = schema.analysis, schema.feedback

    # 0) score-only surveys (NPS, no free text) -> score-derived analysis, no LLM
    score_only = run_score_only(engine, model, pv, limit)

    # 1) unclassified, texted items (idempotent: skip anything already analysed for this model+pv)
    j = f.outerjoin(a, and_(a.c.item_id == f.c.item_id, a.c.model_name == model, a.c.prompt_version == pv))
    q = (select(f.c.item_id, f.c.text, f.c.source_score, f.c.source_scale, f.c.urgency_floor_signal)
         .select_from(j)
         .where(and_(func.length(func.trim(f.c.text)) > 0, a.c.item_id.is_(None)))
         .order_by(f.c.item_id))
    if limit:
        q = q.limit(limit)
    with engine.connect() as c:
        items = [dict(r._mapping) for r in c.execute(q)]
    if not items:
        print("classify: no texted items to do (all already analysed for this model+prompt).")
        return {"classified": 0, **score_only}
    for it in items:
        it["hash"] = _analysis_hash(it["text"], pv, model)

    # 2) reuse any identical text already analysed (content-hash cache)
    hashes = list({it["hash"] for it in items})
    cached: dict[str, tuple] = {}
    with engine.connect() as c:
        # DISTINCT ON the hash: several older rows could share the same hash (e.g. from a
        # prior run), so this keeps exactly one representative result per unique text.
        qc = (select(a.c.analysis_hash, a.c.category, a.c.sentiment, a.c.urgency, a.c.rationale)
              .where(a.c.analysis_hash.in_(hashes)).distinct(a.c.analysis_hash).order_by(a.c.analysis_hash))
        for r in c.execute(qc):
            cached[r.analysis_hash] = (r.category, r.sentiment, r.urgency, r.rationale)
    hash_text = {it["hash"]: it["text"] for it in items}
    todo = [h for h in hashes if h not in cached]

    # 3) one LLM call per NEW unique text, concurrently
    from langchain_openai import ChatOpenAI
    structured = ChatOpenAI(
        model=model, temperature=config.CLASSIFY_TEMPERATURE, seed=config.SEED,
        api_key=config.settings.openai_api_key,
    ).with_structured_output(Classification, include_raw=True)

    print(f"classify: {len(items)} items · {len(hashes)} unique texts · "
          f"{len(cached)} cached · {len(todo)} LLM calls · {workers} workers")
    results: dict[str, tuple] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_invoke, structured, hash_text[h]): h for h in todo}
        for fut in as_completed(futs):
            h = futs[fut]
            try:
                results[h] = fut.result()
            except Exception as e:  # noqa: BLE001
                errors += 1
                results[h] = ("__ERR__", str(e))

    # 4) assemble analysis + llm_calls rows
    claimed: set[str] = set()
    analysis_rows, llm_rows, samples = [], [], []
    tot_in = tot_out = 0
    cats = Counter()
    dis_yes = dis_tot = floored_n = 0
    for it in items:
        h = it["hash"]
        if h in cached:
            cat, sent, urg0, rat = cached[h]
            call_type, in_t, out_t, tot_t, lat, raw = "classify_cache_hit", 0, 0, 0, 0, ""
        else:
            res = results.get(h)
            if not res or res[0] == "__ERR__":
                llm_rows.append({"item_id": it["item_id"], "call_type": "classify", "model_name": model,
                                 "prompt_version": pv, "input": it["text"][:2000], "output": None,
                                 "status": "error", "error": (res[1] if res else "no result")[:500]})
                continue
            parsed, in_t, out_t, tot_t, lat, raw = res
            cat, sent, urg0, rat = parsed.category, parsed.sentiment, parsed.urgency, parsed.rationale
            if h in claimed:  # another item shares this text; the call was already billed
                call_type, in_t, out_t, tot_t, lat, raw = "classify_cache_hit", 0, 0, 0, 0, ""
            else:
                call_type = "classify"
                claimed.add(h)
                tot_in += in_t
                tot_out += out_t

        urg, floored = apply_floor(urg0, it["text"], it["urgency_floor_signal"])
        dis = disagreement(sent, it["source_score"], it["source_scale"])
        if dis is not None:
            dis_tot += 1
            dis_yes += int(dis)
        floored_n += int(floored)
        cats[cat] += 1
        analysis_rows.append({"item_id": it["item_id"], "category": cat, "sentiment": sent, "urgency": urg,
                              "rationale": rat, "confidence": None, "source_score_disagreement": dis,
                              "model_name": model, "prompt_version": pv, "analysis_hash": h})
        llm_rows.append({"item_id": it["item_id"], "call_type": call_type, "model_name": model,
                         "prompt_version": pv, "input": it["text"][:2000], "output": (raw or None) and raw[:2000],
                         "prompt_tokens": in_t or None, "completion_tokens": out_t or None,
                         "total_tokens": tot_t or None, "latency_ms": lat or None, "status": "ok", "error": None})
        if len(samples) < 8:
            samples.append((it["text"][:70], cat, sent, urg, floored))

    # 5) write (in batches of 2000 rows so one giant insert doesn't overload the connection)
    with engine.begin() as c:
        stmt = pg_insert(a).on_conflict_do_nothing(constraint="uq_analysis_item_version")
        for i in range(0, len(analysis_rows), 2000):
            c.execute(stmt, analysis_rows[i:i + 2000])
        for i in range(0, len(llm_rows), 2000):
            c.execute(insert(schema.llm_calls), llm_rows[i:i + 2000])

    # 6) report
    cost = tot_in / 1e6 * _PRICE_IN + tot_out / 1e6 * _PRICE_OUT
    print(f"\n  written: {len(analysis_rows)} analysis rows · {errors} errors")
    print(f"  tokens : {tot_in:,} in + {tot_out:,} out = {tot_in + tot_out:,}  →  est. ${cost:,.4f}")
    print(f"  urgency floor fired: {floored_n} · sentiment↔score disagreement: "
          f"{dis_yes}/{dis_tot} ({(dis_yes / dis_tot if dis_tot else 0):.1%})")
    print(f"  categories: {dict(cats.most_common())}")
    print("  samples:")
    for text, cat, sent, urg, fl in samples:
        print(f"    [{cat} / {sent} / u{urg}{' ⚑floor' if fl else ''}] {text!r}")
    return {"classified": len(analysis_rows), "llm_calls": len(claimed), "errors": errors,
            "tokens_in": tot_in, "tokens_out": tot_out, "est_cost": round(cost, 4),
            "categories": dict(cats), **score_only}
