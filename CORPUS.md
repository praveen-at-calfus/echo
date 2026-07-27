# Building the corpus

The offline corpus-builder produces echo's **15,000-item feedback corpus**
(5k sampled real reviews + 5k synthesized tickets + 5k synthesized surveys) in
the common envelope, plus the order-economics reference view and gold/silver
scaffolding. It is decoupled from Postgres/Milvus — it writes files under
`data/processed/` that a later loader feeds through the ingest pipeline.

## Setup

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[corpus,db,dev]"
cp .env.example .env      # put your OPENAI_API_KEY in .env for real generation
```

Place the 9 downloaded Olist CSVs in **`data/raw/`**. The build writes to
`data/processed/` and caches LLM output in `data/interim/` — all under `data/`,
which is gitignored.

## Run

```bash
# Full build (5k/5k/5k) + verify. Uses OpenAI if OPENAI_API_KEY is set.
python -m echo.corpus

# Tiny dry-run per source (eyeball quality before spending on generation):
python -m echo.corpus --limit 50

# Force the deterministic offline stub (no API key / CI / plumbing tests):
python -m echo.corpus --offline

# Just (re)build the reference view, or just verify an existing build:
python -m echo.corpus --stage econ
python -m echo.corpus --stage verify
```

The **review sampling and order-economics stages need no API key.** Only ticket
and survey text generation calls OpenAI (~10k short, one-time calls, disk-cached
so re-runs are free). Without a key, the builder falls back to the offline stub
and clearly marks `generation_model=offline-stub-v1`.

> **First real run:** do a `--limit 20` run with your key set and read the
> generated Portuguese in `data/processed/tickets.jsonl` before the full build.

## Outputs (`data/processed/`, gitignored)

| File | What |
|---|---|
| `order_economics.parquet` | one row per order — money/fulfillment reference view (the money-engine backbone; **all $/dates originate here**) |
| `reviews.jsonl` · `tickets.jsonl` · `surveys.jsonl` | per-source corpus shards (common envelope) |
| `corpus.jsonl` | concatenation of all three, for the loader |
| `corpus_item.schema.json` | Pydantic-derived JSON Schema of a corpus item |
| `gold_candidates.csv` / `.jsonl` + `gold_instructions.md` | 40 target + 20 reserve stratified candidates with **blank** label columns for a human |
| `manifest.{json,md}` | counts, distributions, messy-flag coverage, money coverage, grounding provenance, input CSV hashes, `corpus_build_id` |

## Reproducibility

Everything keys off one `SEED` in `src/echo/config.py` (also holds the category
mix, messy fractions, MCMC target, NPS thresholds, model, prompt versions).
Sampling and messy assignment are seeded; `item_id` is a `uuid5` of stable refs;
LLM generations are cached to `data/interim/generation_cache/`. Same seed +
prompt versions + model ⇒ identical build. Bump a prompt version to produce a
new build without mutating the old.

## Invariants enforced (see `verify.py`)

LLM never computes a number (all $/dates come from `order_economics`, verified
to match exactly) · raw text never truncated (long items flagged `too_long`;
condensing is downstream) · tickets are NL-only (`source_score` NULL) · synthetic
items carry `synthetic=true` + `grounded_on` provenance · reviews are real ·
Portuguese analyzed in place · gold labels are human-only (builder exports
candidates, never assigns).
