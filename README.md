# echo

**AI customer-feedback intelligence for e-commerce.**

echo ingests raw, messy customer feedback from three channels — product reviews, support tickets, and post-purchase surveys — and turns it into a prioritized, dollar-weighted action list a CX or product leader can act on Monday morning. It auto-categorizes each item, scores sentiment and urgency, surfaces recurring themes, attaches a business-impact figure to each theme, and writes a weekly insight summary.

> **The one thing that makes echo different:** it doesn't just label feedback — it **routes each issue to the team that owns it and attaches the money at stake**, so "we got a lot of complaints" becomes *"Shipping complaints are 34% of negative volume, up 20% week-over-week, with ~$48k of refund exposure — owned by Logistics."*

> **Project status:** built and running. This document is the design source of truth; the full MVP (corpus, classify, money, embeddings, themes, weekly summary, API, RAG, Streamlit dashboard, Docker packaging) plus **JWT authentication with role-based access** is implemented. `CLAUDE.md` holds the authoritative per-stage live status. See **[Running echo locally](#running-echo-locally-for-developers)** to get started.

---

## Who uses it and why

| User | Problem echo solves |
|---|---|
| **VP of CX / Support lead** | Hundreds of feedback items a week, read manually and subjectively. echo gives a ranked, quantified view of what's hurting customers and what it costs. |
| **Product / Engineering** | Which UX/checkout issues actually move conversion? echo ties themes to conversion loss and revenue at risk. |
| **Ops owners (Logistics, Payments, Merchandising…)** | Each gets *their* slice of feedback, already routed, with severity and exposure attached. |

The output is a decision tool, not a dashboard of raw counts.

---

## Core principle: the LLM never computes a number

echo's central design invariant: **the LLM classifies and narrates; every statistic and every dollar figure is computed in SQL and injected into the model.** A model can't hallucinate a number it was never asked to calculate. This split runs through the entire system — themes, urgency ranking, the money engine, and the weekly summary all get their numbers from SQL, and the LLM only writes prose around them.

---

## Architecture — two run modes

echo runs in two modes: **real-time** per feedback item (classify + embed on submission) and a **weekly batch** (money, themes, summary).

```
                    ┌─────────── 3 SOURCES ───────────┐
                    │  reviews    tickets    surveys   │
                    │  (score+NL) (NL only)  (score+NL)│
                    └──────────────┬───────────────────┘
                                   ▼
   ┌───────────────────────────────────────────────────────────────┐
   │ 0. INGEST → normalize to common envelope + messy-input handling │
   │    {source_type, source_score?, text, created_at,              │
   │     order_value?, refund_amount?, order_id?, customer_id?,     │
   │     language}                                                  │
   └──────────────┬────────────────────────────────────────────────┘
                  ▼  (Postgres: feedback = immutable, status=pending)
   ┌──────────── REAL-TIME (per item) ───────────────────────────────┐
   │ 1. CACHE check → 2. ONE LLM call (classify) → 3. cross-check   │
   │    hash hit?      category+sentiment+          LLM sentiment vs │
   │    serve stored.  urgency+rationale            source_score     │
   │                   (few-shot, structured)       → disagree flag  │
   │                        │                                        │
   │   writes: analysis row (versioned) + llm_calls audit           │
   │                        ▼                                        │
   │ 4. EMBED text → pgvector (vector+metadata)                     │
   └──────────────┬─────────────────────────────────────────────────┘
                  ▼
   ┌──────────── WEEKLY BATCH ───────────────────────────────────────┐
   │ 5. MONEY/IMPACT (SQL)  6. THEMES              7. SUMMARY         │
   │    item→category→$        cluster vectors        LLM narrates    │
   │    Direct Exposure        ≥3 items, top 10       SQL numbers     │
   │    + Retention Risk       by $ impact,           (fixed          │
   │    (tiered, degradable)   LLM labels + owner     contract)       │
   └──────────────┬─────────────────────────────────────────────────┘
                  ▼
   ┌─── SERVE ───────────────────────────────────────────────────────┐
   │ FastAPI  →  Streamlit dashboard (thin client)  +  RAG Q&A (bonus)│
   └─────────────────────────────────────────────────────────────────┘
```

If the embeddings/vector index aren't available yet, stages 0–3 still work — only theme extraction and RAG degrade. Graceful degradation is a first-class requirement.

---

## Input model & messy-data handling

Real feedback is messy, and echo treats that as a feature to handle, not an edge case to hope away.

**Every item is free-text.** Numeric scores are only present where they naturally exist:

| Source | Numeric score | Text | Notes |
|---|---|---|---|
| **Review** | ✅ stars 1–5 | ✅ | Public product review |
| **Survey** | ✅ NPS 0–10 / CSAT 1–5 | ✅ | Post-purchase |
| **Ticket** | ❌ none | ✅ | Support contact — no rating exists |

All items normalize into one **common envelope**:

```
source_type   review | ticket | survey
source_score  nullable — present for review/survey, NULL for ticket (normalized to a common scale)
text          messy free-text
created_at    timestamp
order_value?  refund_amount?  order_id?  customer_id?  language
```

**Stage 0 handling (before any LLM cost):**
- **Too short** (< 5 chars) → reject with `422`.
- **Too long** (over the token budget) → an LLM **condense** pass that keeps every concrete complaint, number, product, and request while dropping filler (never a blind truncation, which would cut the actionable tail); flagged `condensed: true`. The **raw text is always preserved immutably** — only the condensed copy is fed to the classifier. Runs `temperature=0` + cached; triggers only for rare over-long items.
- **Spam / gibberish** → flagged.
- **Non-English** → language detected, tagged, analyzed in place.
- **Near-duplicates** → flagged `duplicate_of` (kept, not dropped — duplicate volume is signal).
- **Off-topic / sarcasm** → falls through to the classifier's reject-option (`Other/Unclear`) rather than being force-labeled.

---

## Data & datasets

echo is built on the **Olist Brazilian E-Commerce** public dataset (`olistbr/brazilian-ecommerce`, CC BY-NC) — a real e-commerce corpus that links orders, item prices, freight, payments, customers, and delivery dates.

- **Reviews — real.** Olist ships real customer reviews (`review_score` 1–5 + free-text comment), used directly as the `review` source. Because each review joins back to its order, echo inherits real **money and fulfillment fields** (order value, freight, delivery timing, customer id) — so the corpus reaches **money tier T2/T3** with genuine dollars, not placeholders.
- **Tickets & surveys — synthesized on Olist.** Olist has no support tickets or NPS/CSAT surveys, so echo generates them **against real Olist orders / products / customers / delivery data** — e.g. a synthesized ticket references an actual late or cancelled Olist order; a survey attaches to a real customer's order history. This keeps the whole corpus coherent (same products, same money, same customers) and preserves the source distinctions: **tickets are NL-only (no score); surveys carry a score.**

**Honesty note:** only reviews are real feedback; tickets and surveys are synthetic and flagged as such. Grounding the synthesis in real order data (rather than inventing from scratch) keeps sentiment, urgency, and money plausibly consistent with the real distribution.

**Real-data characteristics (measured on the Olist tables):**
- **Language — Brazilian Portuguese.** echo **analyzes it in place**: the LLM reads Portuguese and emits **English** category labels + rationale (tagged `language: pt`). No translation step → no translation drift. The gold set is hand-labeled from the Portuguese text.
- **Text coverage — ~41%.** Only ~41k of the 99,224 reviews carry a comment; the rest are score-only. The ~41k with text are the classification corpus; score-only reviews still power silver-label sentiment validation and money aggregates.
- **Class skew — positive.** Scores run 5★ 57% · 4★ 19% · 3★ 8% · 2★ 3% · 1★ 12%. The 40-item gold set is therefore **balanced by score/category deliberately**, not sampled at random (a random sample would be mostly 5★).
- **Fulfillment signals for synthesis.** Canceled (625) and shipped-but-not-delivered (1,107) orders, plus `delivered_customer_date` vs `estimated_delivery_date`, are the real basis for synthesized Shipping/Billing tickets.

**Join path (enables the money engine):** `reviews → order_id → orders → customer_id → customers` (de-dup via `customer_unique_id`); `orders → order_items` (`price`, `freight_value`) and `→ order_payments` (`payment_value`) for real dollars; `→ products` for category. Refunds aren't a native field — inferred from canceled orders (`payment_value`).

**Getting the data:** download the Olist dataset from Kaggle (`olistbr/brazilian-ecommerce`) into `data/raw/` (the `data/` folder is gitignored — ~246 MB, CC BY-NC — not redistributed via this repo). Build outputs land in `data/processed/`; the LLM generation cache in `data/interim/`.

---

## Taxonomy → owner → money mechanic

echo uses a **two-layer taxonomy**: a fixed, coarse set of ~10 owner-aligned routing categories (what the classifier assigns, single-label), plus **emergent themes** from clustering that carry the fine-grained specificity. This is deliberate: hand-enumerating 40 categories tanks accuracy and is unmaintainable; ~10 coarse buckets route cleanly and clustering surfaces the long tail automatically.

Each category maps to the team that fixes it and the way it converts to money:

| Category | Owner team | Primary lever | How it becomes $ |
|---|---|---|---|
| **Product Quality** | Merchandising / QA / Vendor mgmt | Vendor scorecard, delist/replace SKU | Return cost + lost margin; repeat-defect SKUs |
| **Shipping & Delivery** | Logistics / Fulfillment | Carrier SLA, reroute, packaging | Reship + refund on lost/damaged; WISMO* contact cost; late-delivery churn |
| **Returns & Refunds** | Reverse-logistics / Finance ops | Fix refund SLA, policy | Refund $ pending + processing cost |
| **Billing & Payment** | Payments / Finance / Fraud | Gateway fix, chargeback flow | Disputed/double-charged $, chargeback fees, failed-checkout GMV |
| **Pricing & Value** | Pricing / Merch / Marketing | Price test, promo, match | Cart-abandon GMV; price-sensitivity churn (modeled) |
| **Website/App UX** | Product / Engineering | Fix checkout bug, search relevance | Conversion loss (sessions × AOV × drop) |
| **Customer Service** | CX / Support ops | Staffing, training, macros | Contact cost + CSAT-driven churn |
| **Availability & Selection** | Inventory / Buying | Restock, add SKU | Lost-sales GMV (unmet demand × price) |
| **Praise** | Marketing / Advocacy | Testimonials, referral | Advocacy value (soft) |
| **Other/Unclear** | Triage | Manual review | None — deliberate reject-option |

*WISMO = "where is my order" contacts — a real, costly support driver.

**Boundary rules** (few-shot examples are chosen to mark these edges, not obvious centers):
- Damaged in transit → **Shipping**; defective / not-as-described → **Product Quality**.
- Returns & Refunds only when the *process* itself is the complaint; otherwise categorize by the underlying issue.
- Money/charge problem → **Billing**; checkout *page/flow* error → **UX**.
- "Too expensive / competitor cheaper" → **Pricing** (not Billing).
- Out-of-stock / "wish you sold X" → **Availability**.
- Wrong item received → **Shipping** *(open question: could warrant its own Order-Accuracy class)*.
- Praise + complaint → categorize by the complaint. Login/account → **UX**.

Single-label keeps scoring clean; when an item is genuinely multi-topic, the classifier's `rationale` names the runner-up topic.

---

## Sentiment, urgency & consistency

- **Sentiment:** 3-class (positive / neutral / negative), scored on the *categorized aspect* so sentiment and category stay coherent on mixed feedback.
- **Urgency 1–5, anchored to business stakes** (hybrid LLM + deterministic floor):
  - **5** — fraud / payment taken but no order / safety hazard / mass checkout outage
  - **4** — individual money at stake or purchase blocked → **Urgent queue**
  - **3** — delayed order needing follow-up
  - **2** — minor dissatisfaction
  - **1** — praise / no action
  - *Deterministic floor:* a ticket containing signals like "fraud / double-charged / never arrived" is floored at 4, reducing the model's subjectivity on the highest-stakes items.
- **One LLM call** returns category + sentiment + urgency + rationale together — cheaper, faster, and the fields stay mutually consistent. The rationale is the interpretability/debugging hook.
- **Consistency via analysis cache:** `hash(normalized_text + prompt_version + model_name)`; on a hit, echo re-serves the stored analysis instead of re-calling the model. This makes identical inputs produce identical outputs by construction (and cuts token cost). "Consistent" is defined as *same category + sentiment + urgency bucket* — `temperature=0` and a fixed `seed` are kept as belt-and-suspenders, not relied on for exact reproducibility.
- **Structured output** via Pydantic + LangChain — guarantees parseable results instead of hoping the model returns valid JSON.

---

## Themes

Weekly, echo turns individual feedback into recurring themes:

1. Pull embeddings from Postgres (pgvector).
2. **Cluster** semantically similar items (cosine-threshold agglomerative / HDBSCAN).
3. Keep clusters with **≥ 3 items**; report the **top 10 by revenue-at-risk** (not by raw count).
4. An LLM **labels** each cluster in the format `<component>: <specific issue>` (e.g. *"Checkout: Apple Pay fails on iOS"*).
5. A **generic-label validator** (banned-phrase list + specificity check) rejects vague labels like "customer issues" and retries.

Each theme carries: label, owner (majority category → team), **$ impact**, item count, and a representative quote. Vector clustering beats keyword search here — *"app crashes on login"* and *"sign-in keeps failing"* share no keywords but one meaning.

---

## Money-weighting engine

All money math is **SQL/Python — never the LLM.**

**Item impact** (the ranking primitive, degrades gracefully):
```
impact = severity_weight(urgency) × value × sentiment_mult
```
- `severity_weight`: urgency 1→5 = {0.1, 0.3, 0.6, 1.0, 1.5}
- `sentiment_mult`: negative 1.0 / neutral 0.4 / positive 0.1
- `value`: the per-category money mechanic; **if the dataset has no money field, `value = 1`** → the whole thing cleanly reduces to *volume × severity*.

Impacts aggregate to **category impact** and **theme impact**.

**Two clearly-separated dollar figures** (the separation *is* the credibility):

1. **Direct Exposure (actual)** — deterministic, from real fields: refund pending, disputed charge, lost/damaged order value, failed-checkout GMV.
2. **Retention Risk (modeled)** — per at-risk customer:
   ```
   retention_risk = customer_value × churn_uplift × category_propensity
   ```
   - `customer_value` = CLV if present → else `AOV × expected_annual_orders` → else a flat assumed value (tiered).
   - **De-duplicated by customer** — one at-risk customer counted once, on their worst issue.
   - **Guardrails:** every assumption lives in one documented config; Retention Risk is always shown *separately* and labeled **"modeled estimate"**; reported as a **low / base / high sensitivity range**, never a false-precision single number.

**Graceful degradation tiers** (echo reports which tier the current data supports):

| Tier | Data available | What echo can report |
|---|---|---|
| T0 | text only | Volume × severity ranking |
| T1 | + rating/NPS (review/survey) | Sentiment-weighted + silver-label validation |
| T2 | + order value / AOV | Direct Exposure in real $ |
| T3 | + refund amount / status / customer | Precise exposure + Retention Risk model |

**This is standard practice, not invention.** The method is textbook **Voice-of-Customer (VoC) / CX economics**. Direct Exposure is ordinary operational **cost-to-serve** (contact cost per ticket, refund/chargeback rates, cart-abandonment GMV). Retention Risk is the industry **"revenue at risk"** metric — `customers at risk × customer lifetime value (CLV/LTV) × churn probability` — the same model enterprise platforms (Qualtrics XM, Medallia) compute, often refined with **driver analysis** (which factors statistically predict churn). The one honest simplification: where those platforms calibrate `churn_uplift` from historical churn data (e.g. survival analysis), echo uses transparent, documented assumptions with a sensitivity range — the correct call absent longitudinal data, and defensible precisely because it's labeled as modeled.

---

## Weekly summary contract

The summary is generated by the LLM but **every number is computed in SQL and injected** — the model only narrates. Fixed sections:

1. **TL;DR**
2. **Volume & sentiment vs prior week** (with a "no baseline yet" fallback for the first week)
3. **Top 5 themes by revenue-at-risk** — each with owner, item count, and one representative quote
4. **Urgent items** (urgency ≥ 4)
5. **Exactly 3 recommended actions** — each tied to a specific dollar figure and named owner

Target: readable aloud in under 90 seconds, every number traceable to SQL. Example action line:
> *Logistics: late-delivery complaints (Blue Dart, NE region) = ~$48k exposure this week, up 20% WoW → escalate carrier SLA review.*

---

## Dashboard & API

**FastAPI** backend. Every request except the public ones below carries a **JWT bearer token** (see [Authentication & roles](#authentication--roles)); the *Access* column is enforced **server-side** (a wrong role gets a `403`, even when driving the endpoint from Swagger UI at `/docs`).

| Endpoint | Purpose | Access |
|---|---|---|
| `POST /auth/register` | Self-register a feedback (GEN-POP) account | public |
| `POST /auth/login` | Exchange email + password for a JWT (OAuth2 password form) | public |
| `GET /auth/me` | The current user | any signed-in |
| `GET /health`, `GET /` | DB + LLM + build id | public |
| `POST /feedback` | Ingest + analyze a single item live | signed-in (GEN-POP or COMPANY) |
| `GET /feedback` | List / filter analyzed feedback | COMPANY = all; GEN-POP = own only |
| `GET /stats/{overview,volume,sentiment,crosstab}` | Aggregate stats | COMPANY |
| `GET /themes` | Themes ranked by revenue-at-risk | COMPANY |
| `GET /urgent` | Urgent queue ranked by $ exposure | COMPANY |
| `GET/POST /summary/weekly` | Read / generate the weekly summary | COMPANY |
| `POST /ask` | RAG Q&A (bonus) | COMPANY |
| `GET /eval/gold` | Model-evaluation report card | COMPANY |

### Authentication & roles

echo has two kinds of user, stored in one `users` table:

- **GEN-POP** — end users who *feed a feedback*. Can submit (`POST /feedback`) and view **only the feedback they submitted**. Public sign-up (`POST /auth/register`) always creates this role.
- **COMPANY** — staff/admins who *see all feedback and analytics*. Full read access to every analytics endpoint. Company accounts are **not** self-registerable; they are provisioned by staff via `python -m echo.auth create-user ... --role company` (or the `seed` command).

Mechanics: passwords are bcrypt-hashed; tokens are signed HS256 (PyJWT) with `JWT_SECRET`. The login route uses the OAuth2 password flow, so Swagger UI shows an **Authorize** button. The security boundary is server-side — the frontend only decides what to *show*, never what's *allowed*. `feedback.submitter_id` (a FK to `users.id`, NULL for the batch corpus) powers GEN-POP "view own".

**Streamlit** dashboard — a **thin client** that talks only to the API (never to the DB or LLM directly), organized as a single router entrypoint (`app.py` + `st.navigation`) with the content pages under `views/`:
- **Landing / login screen** for signed-out visitors (no sidebar): the `echo` wordmark with animated side arc-waves, and a Login button that reveals login / create-account.
- **GEN-POP** sees a single **Feed a feedback** page (submission form + their own past submissions) with a top-right logout and no status chrome.
- **COMPANY** sees the analytics pages in the sidebar — Overview, Urgent Queue, Themes, Weekly Summary, Ask echo, Model Evaluation — plus a DB/LLM status box. Every chart has a title, axis labels, legend, and date range; volume by category & source, sentiment trend, urgent queue by $ exposure, top themes by revenue-at-risk, and source-sliced cross-tabs.
- **UI house style:** no emojis and no em dashes in any user-facing string.

---

## Running echo locally (for developers)

Python 3.13. The `echo` package is run from `src/` (either `PYTHONPATH=src` or `pip install -e .`). `make help` lists every command as a shorter `make` target.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[corpus,db,pipeline,app,frontend,dev]"
cp .env.example .env          # then set values (see below)

# 1) Load the corpus into Postgres (needs the Olist data in data/raw/ first) and seed logins:
PYTHONPATH=src .venv/bin/python -m echo.db            # (re)load data/processed -> Postgres
PYTHONPATH=src .venv/bin/python -m echo.auth seed     # create the demo company + gen_pop accounts

# 2) Run the two services (local is the day-to-day way to run echo):
PYTHONPATH=src .venv/bin/python -m echo.api           # backend, http://localhost:8000 (docs at /docs)
ECHO_API_URL=http://localhost:8000 PYTHONPATH=src .venv/bin/python -m echo.frontend   # dashboard :8501
```

**Environment (`.env`):**
- `OPENAI_API_KEY` — only needed for the live LLM features (submit, generate summary, Ask echo); reads/analytics work without it.
- `JWT_SECRET` — **set this.** It defaults to an insecure dev value and warns at import; anything real must override it. Optional: `JWT_EXPIRE_MINUTES` (default 720).
- `SEED_COMPANY_EMAIL` / `SEED_COMPANY_PASSWORD` / `SEED_GENPOP_EMAIL` / `SEED_GENPOP_PASSWORD` — accounts created by `python -m echo.auth seed`. Defaults: `admin@echo.example` / `admin123` (company) and `user@echo.example` / `user123` (gen_pop). Change these before any real use.

**Auth CLI:** `python -m echo.auth seed | create-user --email ... --password ... --role {company,gen_pop} [--name ...] | list`.

**Docker (packaging verification only):** the app ships as two images plus a Postgres+pgvector container.

```bash
docker compose up --build                                   # -> http://localhost:8501
docker compose exec backend python -m echo.auth seed        # the seed DB dump has no users; seed them
docker compose down                                         # tear down when finished
```

> **Local vs. Docker (important):** local is the primary instance; Docker is only for verifying the packaged deployment, then torn down — never run both at once. They collide on ports 8000/8501/5432 and are *separate databases*, so data submitted to one won't appear in the other. Run **`./scripts/status.sh`** to see which instance owns each port before switching.

---

## Validation approach

- **Gold set:** 40 hand-labeled items, **stratified across the three sources**, for a category confusion matrix. (Small by design — its statistical thinness is acknowledged, and silver labels are the scale answer.)
- **Silver labels:** **real Olist review scores** auto-label thousands of items (≤2 → negative, ≥4 → positive) to validate LLM sentiment *at scale* — "validated against thousands of real ratings," not just 40 samples. (Synthetic survey scores can be checked the same way but aren't independent ground truth; tickets have no score → gold set only.)
- **Sentiment cross-check:** live disagreement rate between LLM sentiment and `source_score` is tracked as a reliability metric.
- **Consistency test:** fixed inputs run twice must produce equivalent output (guaranteed by the analysis cache).

---

## Tech stack

| Layer | Choice |
|---|---|
| LLM | OpenAI API (chat completions, `temperature=0`, fixed `seed`) |
| Orchestration | LangChain (LCEL chains, Pydantic structured output) |
| Backend | FastAPI — **backend container** (API + full pipeline) |
| Auth | JWT bearer (PyJWT, HS256) + bcrypt password hashing; OAuth2 password flow; two roles (GEN-POP / COMPANY), server-side guards |
| Relational DB | PostgreSQL via SQLAlchemy **Core** + parameterized SQL (no ORM session); schema managed by idempotent `metadata.create_all` at startup — no Alembic |
| Vector search | **pgvector** — a PostgreSQL extension; embeddings live in the same DB (no separate vector service) |
| Embeddings | OpenAI embeddings |
| Dashboard | Streamlit — **frontend container** (thin API client) |
| Config/secrets | `.env` + `pydantic-settings` (`.env` gitignored, `.env.example` committed) |
| Packaging | Docker — **exactly 2 containers: `backend` + `frontend`** (Postgres is an external/managed service, not shipped in an app image) |
| Lint/format | ruff (PEP8) |

**Deployment = 2 containers.** The whole app ships as just two images:
- **`backend`** — FastAPI serving the API and running the pipeline (ingest, classify, embed, themes, money engine, weekly summary, RAG).
- **`frontend`** — the Streamlit dashboard, a thin client that talks only to the backend API.

**Data lives outside the app containers.** PostgreSQL — with the **pgvector** extension — holds *both* the relational tables *and* the embeddings, so there is a single data store and **no Milvus/etcd/MinIO stack** to run. In production Postgres is a managed service; for local dev it's a local server (or a throwaway `postgres` container). Folding vectors into Postgres via pgvector is the deliberate simplification that keeps the app to two containers — chosen over a multi-service vector stack because one database is far easier to run, back up, and reason about at echo's scale.

### Data stores

- **Postgres (relational):** `users` (accounts + role for auth) · `feedback` (immutable raw; `submitter_id` FK links a live item to the user who submitted it) · `analysis` (versioned — stores `model_name` + `prompt_version`; re-running a new prompt writes a new row, never mutates raw data) · `llm_calls` (audit: input, output, latency, tokens) · `themes` · `weekly_summary`.
- **Postgres (pgvector):** feedback embeddings + metadata for clustering and RAG retrieval — the **same database**, queried by vector similarity.

---

## Failure & security posture

- Validate cheap things before expensive LLM calls; retry ×3 with exponential backoff on API errors, then a clean human-readable error.
- Failed items are stored `status: pending` and re-processed later — an outage loses nothing.
- Vector index unavailable (e.g. embeddings not built yet) → classification still works; only themes/RAG report degraded.
- No hardcoded secrets (env only; `JWT_SECRET` must be overridden from its dev default); parameterized SQL (SQLAlchemy Core) prevents injection; all user input validated at the API boundary.
- JWT bearer auth with server-side role guards (a wrong role gets `403`, not just a hidden UI element); passwords are only ever stored bcrypt-hashed.

---

## Bonus: RAG Q&A box

An ad-hoc question box — *"What are customers saying about PDF invoices?"*:
```
question → embed → retrieve top-k relevant feedback from Postgres (pgvector)
        → LLM answers, grounded in retrieved snippets, citing feedback IDs
```
Any **number** in the answer still comes from SQL, never the LLM — the anti-hallucination invariant holds even in the bonus. This is the flagship enhancement; core ships first.

---

## Scope & roadmap

**Core (must ship):** ingest + normalize (3 sources, messy input) → classify (category/sentiment/urgency) → embed → themes → money engine → weekly summary → API → dashboard.

**Bonus (never mixed into core scope):** RAG Q&A box (flagship), confidence score + human-review queue, prompt A/B evaluation harness, token-cost dashboard from the `llm_calls` log.
