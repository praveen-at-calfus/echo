"""Turn a cluster into a specific ``<component>: <issue>`` label (LLM + validator).

Plain English: ask the model for a label; if it comes back vague, tell it to be
specific and try again (up to a couple of times). This is the only LLM cost in
the themes stage — one short call per kept cluster.
"""

from __future__ import annotations

import time

from tenacity import retry, stop_after_attempt, wait_exponential

from echo import config
from echo.themes.prompts import build_messages, is_generic


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20), reraise=True)
def _invoke(structured_llm, messages):
    """Call the LLM once with the given messages (auto-retrying on failure), and return the parsed label plus token counts and latency."""
    t0 = time.perf_counter()
    out = structured_llm.invoke(messages)
    latency = int((time.perf_counter() - t0) * 1000)
    parsed = out.get("parsed")
    if parsed is None:
        raise ValueError(f"structured parse failed: {out.get('parsing_error')}")
    raw = out.get("raw")
    um = getattr(raw, "usage_metadata", None) or {}
    return parsed, int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0)), latency


def label_cluster(structured_llm, category: str, quotes: list[str]) -> dict:
    """Label one cluster, retrying while the validator judges the label too generic."""
    in_t = out_t = calls = last_latency = 0
    label = None
    generic = True
    for attempt in range(config.THEME_LABEL_RETRIES + 1):
        parsed, i, o, lat = _invoke(structured_llm, build_messages(category, quotes, stricter=attempt > 0))
        in_t += i
        out_t += o
        calls += 1
        last_latency = lat
        label = parsed.label.strip()
        generic = is_generic(label, category)
        if not generic:
            break
    return {"label": label, "generic": generic, "in_t": in_t, "out_t": out_t,
            "calls": calls, "latency_ms": last_latency}
