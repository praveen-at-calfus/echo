# echo

**AI customer-feedback intelligence for e-commerce.**

echo ingests raw, messy customer feedback from three channels — product reviews, support tickets, and post-purchase surveys — and turns it into a prioritized, dollar-weighted action list a CX or product leader can act on Monday morning. It auto-categorizes each item, scores sentiment and urgency, surfaces recurring themes, attaches a business-impact figure to each theme, and writes a weekly insight summary.

> **The one thing that makes echo different:** it doesn't just label feedback — it **routes each issue to the team that owns it and attaches the money at stake**, so "we got a lot of complaints" becomes *"Shipping complaints are 34% of negative volume, up 20% week-over-week, with ~$48k of refund exposure — owned by Logistics."*

> **Project status:** design specification. This document is the source of truth for the build; implementation follows.

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
   │ 4. EMBED text → Milvus (vector + metadata)                     │
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

If Milvus is unavailable, stages 0–3 still work — only theme extraction and RAG degrade. Graceful degradation is a first-class requirement.

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
- **Too long** (> 2,000 tokens) → truncate + `truncated: true` flag.
- **Spam / gibberish** → flagged.
- **Non-English** → language detected, tagged, analyzed in place.
- **Near-duplicates** → flagged `duplicate_of` (kept, not dropped — duplicate volume is signal).
- **Off-topic / sarcasm** → falls through to the classifier's reject-option (`Other/Unclear`) rather than being force-labeled.

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

1. Pull embeddings from Milvus.
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

**FastAPI** backend:

| Endpoint | Purpose |
|---|---|
| `POST /feedback` | Ingest + analyze a single item live |
| `GET /feedback` | List / filter analyzed feedback |
| `GET /themes` | Themes ranked by revenue-at-risk |
| `GET /urgent` | Urgent queue ranked by $ exposure |
| `GET /summary/weekly` | Latest weekly summary |
| `POST /ask` | RAG Q&A (bonus) |

**Streamlit** dashboard — a **thin client** that talks only to the API (never to the DB or LLM directly):
- Volume by category & source, sentiment trend, urgent queue ranked by $ exposure, top themes by revenue-at-risk.
- Every chart has a title, axis labels, legend, and date range — interpretable by a stakeholder with zero explanation.
- Source-sliced cross-tabs (e.g. "Bug/Product-Quality themes are ticket-heavy; feature asks are survey-heavy").
- Live feedback submission box.

---

## Validation approach

- **Gold set:** 40 hand-labeled items, **stratified across the three sources**, for a category confusion matrix. (Small by design — its statistical thinness is acknowledged, and silver labels are the scale answer.)
- **Silver labels:** review/survey scores auto-label thousands of items (≤2 → negative, ≥4 → positive) to validate LLM sentiment *at scale* — "validated against thousands of real ratings," not just 40 samples. (Tickets have no score, so they're validated via the gold set only.)
- **Sentiment cross-check:** live disagreement rate between LLM sentiment and `source_score` is tracked as a reliability metric.
- **Consistency test:** fixed inputs run twice must produce equivalent output (guaranteed by the analysis cache).

---

## Tech stack

| Layer | Choice |
|---|---|
| LLM | OpenAI API (chat completions, `temperature=0`, fixed `seed`) |
| Orchestration | LangChain (LCEL chains, Pydantic structured output) |
| Backend | FastAPI |
| Relational DB | PostgreSQL (SQLAlchemy ORM + Alembic migrations) |
| Vector DB | Milvus Standalone via Docker Compose (Milvus + etcd + MinIO) |
| Embeddings | OpenAI embeddings |
| Dashboard | Streamlit (API client) |
| Config/secrets | `.env` + `pydantic-settings` (`.env` gitignored, `.env.example` committed) |
| Packaging | Docker Compose (multi-service: DB + vector stack + API + dashboard) |
| Lint/format | ruff (PEP8) |

Milvus + Docker Compose are chosen as **realistic production infra** — multi-service compose with `depends_on`, healthchecks, named volumes, and service-name networking (known trade-off: ~2–4 GB RAM for the vector stack).

### Data stores

- **Postgres:** `feedback` (immutable raw) · `analysis` (versioned — stores `model_name` + `prompt_version`; re-running a new prompt writes a new row, never mutates raw data) · `llm_calls` (audit: input, output, latency, tokens) · `themes` · `weekly_summary`.
- **Milvus:** embeddings + metadata for clustering and RAG retrieval.

---

## Failure & security posture

- Validate cheap things before expensive LLM calls; retry ×3 with exponential backoff on API errors, then a clean human-readable error.
- Failed items are stored `status: pending` and re-processed later — an outage loses nothing.
- Milvus down → classification still works; only themes/RAG report degraded.
- No hardcoded secrets (env only); ORM prevents SQL injection; all user input validated at the API boundary.

---

## Bonus: RAG Q&A box

An ad-hoc question box — *"What are customers saying about PDF invoices?"*:
```
question → embed → retrieve top-k relevant feedback from Milvus
        → LLM answers, grounded in retrieved snippets, citing feedback IDs
```
Any **number** in the answer still comes from SQL, never the LLM — the anti-hallucination invariant holds even in the bonus. This is the flagship enhancement; core ships first.

---

## Scope & roadmap

**Core (must ship):** ingest + normalize (3 sources, messy input) → classify (category/sentiment/urgency) → embed → themes → money engine → weekly summary → API → dashboard.

**Bonus (never mixed into core scope):** RAG Q&A box (flagship), confidence score + human-review queue, pgvector comparison, full-app single-compose dockerization, prompt A/B evaluation harness, token-cost dashboard from the `llm_calls` log.
