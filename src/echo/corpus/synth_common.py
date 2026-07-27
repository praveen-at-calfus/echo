"""Synthesis engine: grounding briefs, generators (OpenAI + offline stub),
a run-once disk cache, and a diversity guard.

Design split (honors the anti-hallucination invariant): Python decides *which*
order, category, timestamp, score, and messy directives; the LLM only renders
natural Portuguese text from a structured grounding brief. Every money/date fact
comes from ``order_economics`` — the model never computes a number. Survey NPS
scores are chosen in Python; the model only writes text matching the target
sentiment.

Reproducibility is guaranteed by the disk cache (keyed on
prompt_version+model+brief+seed): generation runs once, re-runs are free and
identical. The OpenAI ``seed`` is best-effort; the cache is the source of truth.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pandas as pd
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from echo import config
from echo.corpus import utils

OFFLINE_MODEL = "offline-stub-v1"


class SynthText(BaseModel):
    """Structured-output contract for text generation (no numbers)."""

    text: str


# --------------------------------------------------------------------------- #
# Grounding facts / briefs
# --------------------------------------------------------------------------- #
def _iso(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        v = v.to_pydatetime()
    return v.date().isoformat() if isinstance(v, datetime) else str(v)


def _num(v):
    return None if v is None or pd.isna(v) else round(float(v), 2)


def order_facts(row) -> dict:
    """Extract the grounding facts a synthetic item may reference."""
    return {
        "order_ref": str(row.order_id)[:8],
        "product_category_en": None if pd.isna(row.product_category_en) else str(row.product_category_en),
        "order_value": _num(row.order_value),
        "freight_value": _num(row.freight_value),
        "refund_amount": _num(row.refund_amount_proxy),
        "payment_type": None if pd.isna(row.payment_type) else str(row.payment_type),
        "payment_installments": None if pd.isna(row.payment_installments) else int(row.payment_installments),
        "city": None if pd.isna(row.customer_city) else str(row.customer_city),
        "state": None if pd.isna(row.customer_state) else str(row.customer_state),
        "purchase_date": _iso(row.purchase_ts),
        "estimated_date": _iso(row.estimated_ts),
        "delivered_date": _iso(row.delivered_ts),
        "lateness_days": None if pd.isna(row.lateness_days) else int(row.lateness_days),
        "fulfillment_outcome": str(row.fulfillment_outcome),
    }


def cache_key(brief: dict, seed: int, model: str) -> str:
    payload = json.dumps({"brief": brief, "seed": seed, "model": model}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #
class OpenAIGenerator:
    """Real generation via a cost-efficient OpenAI chat model (LangChain)."""

    def __init__(self, model: str, api_key: str):
        self.model = model
        self._api_key = api_key

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20), reraise=True)
    def generate(self, brief: dict, seed: int) -> dict:
        from langchain_openai import ChatOpenAI

        from echo.prompts import build_messages

        llm = ChatOpenAI(
            model=self.model,
            temperature=config.GEN_TEMPERATURE,
            seed=seed,
            api_key=self._api_key,
        ).with_structured_output(SynthText)
        out: SynthText = llm.invoke(build_messages(brief))
        return {"text": out.text.strip(), "generation_model": self.model}


class OfflineStubGenerator:
    """Deterministic, grounded Portuguese stub — for dev/CI/dry-runs with no key.

    NOT the production path: text is templated and clearly marked
    ``generation_model=offline-stub-v1``. The real corpus uses OpenAIGenerator.
    """

    def generate(self, brief: dict, seed: int) -> dict:
        from echo.corpus.stub_text import render_stub

        return {"text": render_stub(brief, seed), "generation_model": OFFLINE_MODEL}


class CachedGenerator:
    """Wraps a generator with a run-once disk cache + provenance stamping."""

    def __init__(self, inner):
        self.inner = inner
        self.model = getattr(inner, "model", OFFLINE_MODEL)
        self.hits = 0
        self.misses = 0
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def generate(self, brief: dict, seed: int) -> dict:
        key = cache_key(brief, seed, self.model)
        path = config.CACHE_DIR / f"{key}.json"
        if path.exists():
            self.hits += 1
            return json.loads(path.read_text(encoding="utf-8"))
        self.misses += 1
        rec = self.inner.generate(brief, seed)
        rec["cached_at"] = datetime.now(timezone.utc).isoformat()
        rec["prompt_version"] = brief.get("prompt_version")
        path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return rec


def make_generator(settings=config.settings) -> CachedGenerator:
    if settings.use_offline:
        return CachedGenerator(OfflineStubGenerator())
    return CachedGenerator(OpenAIGenerator(settings.model, settings.openai_api_key))


class RecordingGenerator:
    """Runs the deterministic build logic WITHOUT calling the LLM, recording every
    ``(brief, seed)`` that will be requested. Returns a unique dummy per call so the
    diversity guard never triggers a retry — the recorded set is exactly the
    first-attempt briefs, which can then be generated concurrently.
    """

    def __init__(self, model: str):
        self.model = model
        self.requests: list[tuple[dict, int]] = []
        self._n = 0

    def generate(self, brief: dict, seed: int) -> dict:
        self.requests.append((brief, seed))
        self._n += 1
        return {"text": f"__rec__{seed}__{self._n}", "generation_model": self.model}


def warm_cache(cached: CachedGenerator, requests, workers: int = config.GEN_WORKERS) -> dict:
    """Concurrently populate the disk cache for the given (brief, seed) requests."""
    from concurrent.futures import ThreadPoolExecutor

    uniq: dict[str, tuple[dict, int]] = {}
    for brief, seed in requests:
        uniq[cache_key(brief, seed, cached.model)] = (brief, seed)
    todo = [bs for k, bs in uniq.items() if not (config.CACHE_DIR / f"{k}.json").exists()]

    def _one(bs) -> bool:
        try:
            cached.generate(*bs)
            return True
        except Exception:  # noqa: BLE001
            return False

    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(_one, todo):
            ok += int(r)
    return {
        "unique": len(uniq),
        "cached_already": len(uniq) - len(todo),
        "generated": ok,
        "failed": len(todo) - ok,
    }


# --------------------------------------------------------------------------- #
# Diversity guard
# --------------------------------------------------------------------------- #
class DiversityGuard:
    """Rejects unintended exact-normalized duplicates so synthesis can retry."""

    def __init__(self):
        self._seen: set[str] = set()
        self.residual_near_dupes = 0

    def check(self, text: str) -> bool:
        return utils.normalize_text(text) not in self._seen

    def add(self, text: str) -> None:
        self._seen.add(utils.normalize_text(text))

    def note_residual(self) -> None:
        self.residual_near_dupes += 1
