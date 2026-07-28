# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What echo is

echo is **AI customer-feedback intelligence for e-commerce**: it ingests messy customer feedback from three channels (product **reviews**, support **tickets**, post-purchase **surveys**), classifies each item (category / sentiment / urgency), attaches the **money at stake**, surfaces recurring **themes**, and writes a weekly summary — a prioritized, dollar-weighted action list.

**`README.md` is the authoritative design spec — read it first.** It defines the taxonomy→owner→money table, the money-engine formulas, urgency anchors, the weekly-summary contract, and the endpoint list. `CORPUS.md` documents the dataset build.

**Target shape:** exactly **2 app containers** — `backend` (FastAPI + the whole pipeline) and `frontend` (Streamlit, a thin client that talks only to the API) — plus **PostgreSQL with the pgvector extension** as an external data store (vectors live in Postgres, *not* Milvus).

## Current state (keep this section honest as you build)

- **Built:** the offline **corpus builder** (`src/echo/corpus/`), **Postgres persistence** (`src/echo/db/`), the **classify** stage (`src/echo/classify/`), the **money engine** (`src/echo/money/`), and the **embeddings** stage (`src/echo/embed/` + `src/echo/db/vector.py`). A 15,000-item corpus is loaded; all 15,000 items are classified (`analysis` full, `gpt-4o-mini`/`classify_v1`) and all 14,765 texted items are embedded (`embeddings` table, `text-embedding-3-small`, 1536-d, HNSW cosine index).
- **Money engine** is pure SQL/Python (no table, no LLM): `python -m echo.money [--week|--demo-week]` reports per-category Direct Exposure (deterministic) + modeled Retention Risk (low/base/high) + coverage tier (currently T3). `money.engine.summary()` / `urgent_items()` are reused downstream (summary, and the future `/urgent` endpoint).
- **pgvector is installed** on the local `postgresql@16` (built from source against pg16 — the brew bottle only shipped pg17/18 files). `db/vector.py::create_all()` runs `ensure_extension()` before DDL; `embed.nearest()` does cosine top-k.
- **Themes** (`src/echo/themes/`, `python -m echo.themes [--week|--trend N|--threshold]`): per week, agglomerative cosine clustering (threshold 0.40) over embeddings of actionable items (negative+neutral), keep clusters ≥3, rank top-10 by revenue-at-risk (`money.engine.exposure_for_items`), LLM writes a specific `<component>: <issue>` label with a generic-label validator (retry ×2), owner = majority category→team. Writes `themes` + `theme_members` idempotently (a re-run replaces the week). Populated for `DEMO_WEEK` + 4 preceding weeks (33 themes). Clustering/ranking are $0/SQL; only labelling costs tokens (~$0.002 for 5 weeks).
- **Weekly summary** (`src/echo/summary/`, `python -m echo.summary [--week]`): SQL computes every number (volume + sentiment WoW, top drivers by revenue-at-risk, urgent queue); the LLM only narrates + picks a driver-category per action; echo injects the `$` (authoritative per-category exposure) and owner. Writes one upserted row per week to `weekly_summary` (+ `llm_calls` audit). Uses real themes when present (`source: theme`), else falls back to categories-by-revenue-at-risk (`source: category_fallback`).
- **API** (`src/echo/api/`, `python -m echo.api` or `uvicorn echo.api.main:app`): FastAPI, SQLAlchemy Core + parameterized SQL, no ORM session. Endpoints: `/health` (db+llm+build_id), `/stats/{overview,volume,sentiment,crosstab}`, `/themes`, `/urgent`, `/summary/weekly`, `GET /feedback` (filter+paginate), `POST /feedback` (live classify+embed+money, gated on `/health.llm`), `POST /ask` (RAG, gated on `/health.llm`). Reuses `classify.classify_text()` + `embed.embed_texts()`. Every figure is SQL-computed. All endpoints verified against the live DB.
- **RAG / "ask echo"** (`src/echo/rag/`, `python -m echo.rag "question"` or `POST /ask`): embed the question (`embed.embed_texts`) → pgvector cosine top-k over `embeddings` (`rag/retrieve.py`, query vector bound as a `CAST(:qvec AS vector)` literal — no stored row on the query side) → LLM writes a grounded answer + a list of `item_id`s it drew on (`rag/prompts.py::RagAnswer`, hallucinated ids dropped) → echo appends a stats block computed purely in SQL over the same retrieved set (sentiment split, top category, `money.exposure_for_items` dollar figures) — the model never emits a number. One `llm_calls` audit row per call (`call_type='rag'`). Verified live: "late deliveries?" and a billing-charges question both returned grounded, cited, SQL-numbered answers via both the CLI and `POST /ask`.
- **Frontend** (`src/echo/frontend/`, `python -m echo.frontend` or `streamlit run src/echo/frontend/app.py`; base URL from `ECHO_API_URL`, default `http://localhost:8000`): Streamlit multipage dashboard, a genuinely thin client — every number/answer comes from `api_client.py` calling the FastAPI backend, nothing computed locally. `app.py` = Overview (KPI tiles, money-at-stake tiles, volume by category/source, sentiment split + weekly trend, category×source heatmap); `pages/` = Urgent Queue, Themes (chart + expandable cards), Weekly Summary, Live Feedback (`POST /feedback`, gated on `/health.llm`), Ask echo (`POST /ask`, gated). `charts.py` builds Plotly figures per the dataviz-skill method (one hue for magnitude bars/heatmap, reserved good/neutral/critical status colors for the 3-way sentiment split/trend, titles+axes+legends+automargin on every figure); `common.py` holds the shared sidebar health badge + a compact-currency (`R$115k`) money-metric row. Verified live end-to-end with a real backend + Playwright/Chromium: all 6 pages screenshotted, plus real interactive round-trips — submitted a live ticket (correctly floored to urgency 5 on the fraud/double-charge phrasing) and asked a real question through the UI (grounded, cited, dollar-figured answer rendered).
- **Docker packaging** (`docker-compose.yml`, `docker/{backend,frontend}.Dockerfile`, `docker/seed/`): the target 2-app-container shape is real — `docker compose up --build` → `db` (`pgvector/pgvector:pg16`, named volume `pgdata`) → `backend` (`uvicorn`-only entrypoint; the corpus loader must never run against a seeded DB) → `frontend`. First boot on an empty volume auto-restores `docker/seed/00_extensions.sql` (`CREATE EXTENSION vector`) + `docker/seed/10_echo_seed.sql.gz` (a `pg_dump --exclude-table-data=embeddings` of the live DB — full schema + all data *except* embeddings rows, kept out because RAG needs an `OPENAI_API_KEY` regardless, so shipping them for free buys nothing; `python -m echo.embed` inside the backend container backfills them once a key is set). `docker compose down -v` wipes the volume to re-seed from scratch. An opt-in `seed` service (`--profile rebuild`) regenerates the dump from a running `db`. `api/main.py` gained a `lifespan` startup hook (`vector.create_all` then `schema.metadata.create_all`, in that order — the extension must exist before the unscoped create_all, since importing `vector` registers the `embeddings` table on the same shared metadata) as a safety net so the backend is correct even without the seed. **Verified live**: built both images, brought up all 3 containers on a fresh volume, confirmed the seed restored (15,003 feedback/analysis rows, 0 embeddings rows as designed), screenshotted the dashboard working end-to-end through the container network, and explicitly tested the no-`OPENAI_API_KEY` path (`/health` → `llm:false`, frontend shows a clean gating banner, no crash) before restoring the key and confirming recovery. Skipped Git LFS for the 18MB seed file (well under GitHub's 100MB hard limit) to avoid a global git-config change (`git lfs install`) the safety protocol disallows without the user opting in.
- **Not yet built:** none — all MVP stages (corpus → classify → money → embeddings → themes → summary → API → RAG → frontend → Docker) are built and verified. Optional/outstanding: the 40-item gold set is still unlabeled (`data/processed/gold_candidates.csv`).

## Setup & commands

Python **3.13** venv at `.venv`. The `echo` package is **not** installed editable — either prefix runs with `PYTHONPATH=src`, or `pip install -e .`.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[corpus,db,pipeline,app,frontend,dev]"   # extras: corpus, db, pipeline, app, frontend, dev
cp .env.example .env                             # set OPENAI_API_KEY (only needed for LLM stages)

# Run a stage (every stage is a package with a __main__):
PYTHONPATH=src .venv/bin/python -m echo.corpus              # full corpus build + verify
PYTHONPATH=src .venv/bin/python -m echo.corpus --limit 50   # tiny dry-run per source
PYTHONPATH=src .venv/bin/python -m echo.corpus --offline    # deterministic stub, no API calls
PYTHONPATH=src .venv/bin/python -m echo.corpus --stage econ|verify|all   # single stage
PYTHONPATH=src .venv/bin/python -m echo.db                  # (re)load data/processed -> Postgres
PYTHONPATH=src .venv/bin/python -m echo.db --keep           # append instead of truncate+reload
PYTHONPATH=src .venv/bin/python -m echo.api                 # run the backend (uvicorn, :8000)
ECHO_API_URL=http://localhost:8000 PYTHONPATH=src .venv/bin/python -m echo.frontend   # run the dashboard (:8501)

# Or the whole thing in 2 containers + Postgres/pgvector, zero local Python setup:
docker compose up --build                        # -> http://localhost:8501 (needs Docker Desktop running)
docker compose down -v                           # wipe the volume (forces a re-seed on next `up`)
docker compose --profile rebuild run --rm seed   # regenerate docker/seed/10_echo_seed.sql.gz from the running db

.venv/bin/ruff check src/                        # lint (config in pyproject; must be clean)
psql -h localhost -d echo                        # DB console
```

Long/expensive runs (full LLM builds) should go to the background with logs to a file — `tail` them, don't dump them into context.

## Architecture — the load-bearing ideas (read multiple files to grasp)

1. **Anti-hallucination invariant (non-negotiable).** The LLM only *classifies* and *writes prose*; **every number and dollar figure is computed in SQL/Python and injected** into the model. Never let an LLM emit a figure. `order_economics` is the single money source of truth (all $/dates originate there).

2. **`config.py` is the single source of truth for every knob** — `SEED`, the fixed **10-category taxonomy** (`CATEGORIES`), corpus sizes, synthesis mixes, MCMC/cluster thresholds, prompt-version strings, model name, and `DATABASE_URL`. New assumptions go here, never inline.

3. **The common envelope.** `schemas/envelope.py::CorpusItem` is the one shape every feedback item normalizes to; its validators *enforce* invariants (tickets carry no score, reviews/surveys always do; `created_at` inside the Olist window; category ∈ the fixed 10). Reused by the corpus builder, the loader, and every future stage.

4. **Data substrate.** All data lives under `data/` (gitignored) in `raw/` (9 Olist CSVs), `processed/` (build outputs: `corpus.jsonl`, `order_economics.parquet`, gold set, manifest), `interim/` (the run-once LLM disk cache). Postgres db `echo` holds 8 tables (`db/schema.py`, SQLAlchemy Core, one `metadata`): `order_economics` (one row per order — the money/fulfillment backbone), `feedback` (the 15k items; provenance + messy-flags as JSONB), `gold_candidates`, and the pipeline tables `analysis` (versioned classify output), `llm_calls` (audit), `themes` + `theme_members`, `weekly_summary`.

5. **Reuse these patterns; don't reinvent.**
   - `corpus/synth_common.py` is the template for **any** LLM stage: `ChatOpenAI(...).with_structured_output(PydanticModel)` + tenacity retry + a disk cache keyed by content hash + a `RecordingGenerator`→`warm_cache` two-phase pattern for concurrent batch throughput.
   - `db/load.py` has the SQLAlchemy Core bulk-insert / batching / `_nonul` (strip NUL bytes Postgres rejects) patterns for any DB writer. The loader does `create_all` (never drops) + `TRUNCATE`s **only** the corpus tables — so it never wipes populated pipeline tables.

6. **Reproducibility.** One `SEED` drives all sampling; `item_id` is a `uuid5` (idempotent re-runs); LLM outputs are cached to disk (run-once) and, for analysis, to a table **versioned by `model_name` + `prompt_version`** (bump the prompt version to re-run without mutating prior results). `corpus/verify.py` runs 14 invariant checks and gates the build.

## When I ask you to "explain" something (explanation mode)

Any time my prompt is an "explain this" request — the project, a stage, a decision, a chunk of code, an error, whatever — **default to explaining it to a smart person with zero technical context**, someone who knows only "we're building an AI agent to do something." Do this *unless* I explicitly say "explain technically" / "for engineers." Rules:

- **Start from zero.** Assume no knowledge of the codebase, the stack, or the jargon. Don't reference file names, table names, or library names as if they're understood.
- **Translate every unavoidable technical term inline**, in plain words, the first time it appears (e.g. "a database — a well-labeled digital filing cabinet").
- **Lead with the *why*, not the *how*.** What real-world problem does this solve for a real person (a store owner, a CX manager)? Use concrete analogies and the actual customer-feedback scenario, not abstractions.
- **Follow a shape:** what it is → why it matters → where we are (built vs. still-to-come, honestly) → a one-line "if you remember one thing" summary at the end.
- **Stay truthful and grounded** — same anti-hallucination discipline as the product: real numbers, honest progress, no glossing over what's unfinished. Simplify, don't fabricate.
- **Offer, at the end, to turn it into a one-page visual** they could show in a meeting.

Keep it warm and readable — narrative prose over dense bullet-walls, minimal formatting.

## Conventions

- **src-layout**; each stage is a package under `src/echo/` exposing a `__main__.py`, run as `python -m echo.<stage>`.
- Every module opens with a **plain-English + light-technical docstring** — match that style.
- **New LLM stage:** add its prompt-version + knobs to `config.py`; write to a versioned table; cache by content hash; mirror `synth_common.py`.
- **New table:** add to `db/schema.py` (single `metadata`); decide whether the loader should truncate it.
- **The dataset is real-grounded** (that's what makes demos credible): reviews are genuine Brazilian-Portuguese Olist reviews; tickets + surveys are LLM-synthesized but grounded on real orders and flagged `synthetic`. Text is Portuguese, analyzed **in place** (the LLM reads PT, emits English labels).
