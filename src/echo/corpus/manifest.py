"""Build manifest — the "report card" for one corpus build.

Plain English: after a build, this writes a short human-readable summary (how
many items, the sentiment/score/messy breakdowns, money coverage) plus a JSON
version. Technically: aggregates stats over all items + per-stage stats, stamps
a deterministic ``corpus_build_id``, and records a sha256 of every input CSV so
we can tell if the raw data changed underneath us.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from echo import config
from echo.schemas.envelope import CorpusItem


def _sha256(path) -> str:
    """Compute the sha256 hash of a file's contents (read in chunks so large files don't need to fit in memory) and return it as a hex string."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def corpus_build_id(model: str) -> str:
    """Derive a short, stable build id from the seed, prompt versions, generation model, and builder version, so identical settings always produce the same id."""
    payload = "|".join(
        [str(config.SEED), config.PROMPT_VERSION_TICKETS, config.PROMPT_VERSION_SURVEYS,
         model, config.BUILDER_VERSION]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _dist(items, key):
    """Count how many items fall into each distinct value returned by key(item), and return that as a dict."""
    return dict(Counter(key(it) for it in items))


def build_manifest(items: list[CorpusItem], stage_stats: dict, generator, build_id: str) -> dict:
    """Assemble the full build manifest dict: counts, distributions, messy-flag stats, money coverage, generation stats, and input file hashes, for one corpus build."""
    by_source = _dist(items, lambda it: it.source_type)
    scored = [it for it in items if it.source_score is not None]
    return {
        "corpus_build_id": build_id,
        "builder_version": config.BUILDER_VERSION,
        "seed": config.SEED,
        "target": config.MCMC_TARGET,
        "generation_model": generator.model,
        "prompt_versions": {
            "tickets": config.PROMPT_VERSION_TICKETS,
            "surveys": config.PROMPT_VERSION_SURVEYS,
        },
        "counts": {"total": len(items), **by_source},
        "silver_labels": _dist(items, lambda it: it.silver_label),
        "score_dist": {
            "review_stars": _dist([i for i in scored if i.source_scale == "star_1_5"],
                                  lambda it: int(it.source_score)),
            "survey_nps": _dist([i for i in scored if i.source_scale == "nps_0_10"],
                                lambda it: int(it.source_score)),
        },
        "intended_category_tickets": _dist(
            [i for i in items if i.source_type == "ticket"], lambda it: it.intended_category
        ),
        "language": _dist(items, lambda it: it.language),
        "messy_flags": {
            flag: sum(1 for it in items if getattr(it.messy, flag))
            for flag in ["too_short", "too_long", "spam", "gibberish", "non_target_language",
                         "sarcasm", "off_topic", "multi_topic", "urgency_floor_signal"]
        },
        "duplicates": sum(1 for it in items if it.messy.duplicate_of),
        "long_text": {
            ">1000": sum(1 for it in items if len(it.text) > 1000),
            ">2000": sum(1 for it in items if len(it.text) > 2000),
            "max_chars": max((len(it.text) for it in items), default=0),
        },
        "money_coverage": {
            "with_order_value": _rate(items, lambda it: it.order_value is not None),
            "with_refund_amount": _rate(items, lambda it: it.refund_amount is not None),
            "tier_note": "T2 where order_value present; T3 where refund/customer present.",
        },
        "generation": {
            "cache_hits": generator.hits,
            "cache_misses": generator.misses,
            "residual_near_dupes": stage_stats.get("guard_residual", 0),
        },
        "stages": stage_stats,
        "inputs_sha256": {
            name: _sha256(config.RAW_DIR / fname) for name, fname in config.RAW_FILES.items()
        },
    }


def _rate(items, pred) -> float:
    """Compute the fraction of items for which pred(item) is true, rounded to 4 decimal places (0 if there are no items)."""
    return round(sum(1 for it in items if pred(it)) / max(len(items), 1), 4)


def write_manifest(manifest: dict) -> None:
    """Write the manifest to both a JSON file (machine-readable) and a Markdown file (human-readable summary)."""
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (config.PROCESSED_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
    )
    (config.PROCESSED_DIR / "manifest.md").write_text(_render_md(manifest), encoding="utf-8")


def _render_md(m: dict) -> str:
    """Format the manifest dict as a short human-readable Markdown report and return it as a string."""
    c = m["counts"]
    mf = m["messy_flags"]
    lines = [
        f"# echo corpus build `{m['corpus_build_id']}`",
        "",
        f"- builder v{m['builder_version']} · seed `{m['seed']}` · target `{m['target']}`",
        f"- generation model: `{m['generation_model']}` "
        f"(cache hits {m['generation']['cache_hits']} / misses {m['generation']['cache_misses']})",
        "",
        "## Counts",
        f"- **total {c['total']}** — review {c.get('review', 0)} · "
        f"ticket {c.get('ticket', 0)} · survey {c.get('survey', 0)}",
        f"- silver labels: {m['silver_labels']}",
        "",
        "## Messy-input coverage",
        f"- long >1000 chars: {m['long_text']['>1000']} · >2000: {m['long_text']['>2000']} "
        f"· max {m['long_text']['max_chars']} chars",
        f"- spam {mf['spam']} · gibberish {mf['gibberish']} · non-PT {mf['non_target_language']} "
        f"· sarcasm {mf['sarcasm']} · off-topic {mf['off_topic']} · multi-topic {mf['multi_topic']}",
        f"- urgency-floor signals: {mf['urgency_floor_signal']} · duplicates: {m['duplicates']}",
        "",
        "## Money coverage",
        f"- with order_value: {m['money_coverage']['with_order_value']:.1%} · "
        f"with refund_amount: {m['money_coverage']['with_refund_amount']:.1%}",
        "",
        "## Distributions",
        f"- review stars: {m['score_dist']['review_stars']}",
        f"- survey NPS: {m['score_dist']['survey_nps']}",
        f"- ticket intended-category: {m['intended_category_tickets']}",
    ]
    return "\n".join(lines) + "\n"
