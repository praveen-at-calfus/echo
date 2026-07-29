# echo

**AI customer-feedback intelligence for e-commerce.**

echo ingests raw, messy customer feedback from three channels — product reviews, support tickets, and post-purchase surveys — and turns it into a prioritized, dollar-weighted action list a CX or product leader can act on Monday morning. It auto-categorizes each item, scores sentiment and urgency, surfaces recurring themes, attaches a business-impact figure to each theme, and writes a weekly insight summary.

> **The one thing that makes echo different:** it doesn't just label feedback — it **routes each issue to the team that owns it and attaches the money at stake**, so "we got a lot of complaints" becomes *"Shipping complaints are 34% of negative volume, up 20% week-over-week, with ~$48k of refund exposure — owned by Logistics."*

> **Project status:** built and running. This document is the design source of truth and the onboarding guide for developers joining the project. The full MVP (corpus, classify, money, embeddings, themes, weekly summary, API, RAG, Streamlit dashboard, Docker packaging, CI image publishing) plus **JWT authentication with role-based access** is implemented. `CLAUDE.md` holds the authoritative, continuously-updated per-stage status. `CORPUS.md` documents the dataset build in depth. Jump straight to **[Running echo locally](#running-echo-locally-for-developers)** to get started, or **[New to this codebase?](#new-to-this-codebase-start-here)** for a five-minute orientation.

---

## Table of contents

- [Who uses it and why](#who-uses-it-and-why)
- [Core principle: the LLM never computes a number](#core-principle-the-llm-never-computes-a-number)
- [New to this codebase? Start here](#new-to-this-codebase-start-here)
- [Architecture — two run modes](#architecture--two-run-modes)
- [Project layout](#project-layout)
- [Input model & messy-data handling](#input-model--messy-data-handling)
- [Data & datasets](#data--datasets)
- [Taxonomy → owner → money mechanic](#taxonomy--owner--money-mechanic)
- [Sentiment, urgency & consistency](#sentiment-urgency--consistency)
- [Themes](#themes)
- [Money-weighting engine (the formulas, explained)](#money-weighting-engine-the-formulas-explained)
- [Weekly summary contract](#weekly-summary-contract)
- [Dashboard & API](#dashboard--api)
- [Authentication & roles](#authentication--roles)
- [Running echo locally (for developers)](#running-echo-locally-for-developers)
- [Makefile reference](#makefile-reference)
- [Docker packaging & image storage](#docker-packaging--image-storage)
- [CI/CD: automated image publishing](#cicd-automated-image-publishing)
- [Contributing: branches, commits & pull requests](#contributing-branches-commits--pull-requests)
- [Validation approach](#validation-approach)
- [Tech stack](#tech-stack)
- [Failure & security posture](#failure--security-posture)
- [Bonus: RAG Q&A box](#bonus-rag-qa-box)
- [Scope & roadmap](#scope--roadmap)

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

If you remember one rule about this codebase while reading or writing code, it's this one. Any time you see a piece of code building a prompt, check what data is being handed to the model — it should be text to read and classify, never a number for it to compute or repeat back from thin air.

---

## New to this codebase? Start here

A five-minute mental model before you open any file:

1. **Three kinds of feedback come in** (a star review, a support ticket, a post-purchase survey). They all get normalized into one shape (the "common envelope") so the rest of the system doesn't care which channel something came from.
2. **One LLM call reads the text** and returns a category (which team owns this), a sentiment (positive/neutral/negative), an urgency score (1–5), and a short rationale. This result is cached and versioned so re-running the same text never costs money twice or gives a different answer.
3. **Plain SQL and Python attach a dollar figure** to each item, based on real order data where it exists (refund amount, order value) — never guessed by the LLM.
4. **Weekly, similar complaints get grouped into "themes"** (e.g. many different wordings of "my order arrived late" become one theme) by clustering their embeddings (a numeric fingerprint of meaning, not keywords) and letting the LLM write one short label for the group.
5. **Everything gets served two ways**: a FastAPI backend for numbers/data, and a Streamlit dashboard that just displays what the API returns — it never computes anything itself.
6. **Two user roles exist**: a `gen_pop` (general public) account can submit feedback and see only their own; a `company` account sees everything and every analytics page.

Once that sequence is in your head, the [project layout](#project-layout) below tells you exactly which folder does which of those six steps, and the [architecture diagram](#architecture--two-run-modes) shows how they connect end to end.

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
   │ FastAPI (JWT auth)  →  Streamlit dashboard (thin client)  +  RAG Q&A (bonus)│
   └─────────────────────────────────────────────────────────────────┘
```

If the embeddings/vector index aren't available yet, stages 0–3 still work — only theme extraction and RAG degrade. Graceful degradation is a first-class requirement.

---

## Project layout

```
echo/
├── src/echo/
│   ├── config.py          # single source of truth: taxonomy, thresholds, model name, DATABASE_URL
│   ├── schemas/           # the "common envelope" shape every feedback item normalizes to
│   ├── corpus/            # offline builder: turns raw Olist sales data into the 15k-item dataset
│   ├── db/                # SQLAlchemy Core schema + the loader that puts the corpus into Postgres
│   ├── classify/          # stage 1-3: one LLM call per item -> category/sentiment/urgency + rationale
│   ├── embed/             # stage 4: text -> vector, stored in pgvector
│   ├── money/             # stage 5: SQL/Python-only dollar-impact engine (Direct Exposure + Retention Risk)
│   ├── themes/            # stage 6: cluster embeddings into weekly themes, LLM labels each cluster
│   ├── summary/           # stage 7: SQL computes the numbers, LLM narrates the weekly report
│   ├── rag/               # bonus: "Ask echo" question answering over retrieved feedback
│   ├── auth/              # JWT + bcrypt user accounts, roles (gen_pop / company), CLI (seed/create-user/list)
│   ├── api/                # FastAPI app: routers/, schemas.py, deps.py (auth guards), main.py (app + lifespan)
│   ├── prompts/           # shared prompt-building helpers reused by more than one stage
│   └── frontend/          # Streamlit dashboard: app.py (router) + views/ (one file per page)
├── docker/                # Dockerfiles for the 2 app images + the Postgres seed data
├── docker-compose.yml     # local packaged demo: db + backend + frontend
├── scripts/               # status.sh (port conflict checker), refresh-docker.sh (rebuild + reseed)
├── Makefile               # `make help` — shorthand for every command in this README
├── .github/workflows/     # CI: builds + publishes the two Docker images to GHCR
├── CLAUDE.md              # live, continuously-updated per-stage build status (read this for "what's true right now")
├── CORPUS.md              # deep-dive on how the 15,000-item dataset was built
└── README.md              # you are here — the design spec + developer onboarding doc
```

Every stage package under `src/echo/` follows the same shape: a `__main__.py` so it can be run standalone as `python -m echo.<stage>`, and a module docstring explaining what it does in plain English before the technical detail. When adding a new stage, mirror this pattern rather than inventing a new one.

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

**As built:** the working corpus is **15,000 items** — 5,000 real reviews (MCMC-sampled to a representative score × category × length distribution) + 5,000 synthesized tickets + 5,000 synthesized surveys — loaded into Postgres, all classified, and (text-bearing items) embedded. `CORPUS.md` documents the build.

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

The fixed list of 10 categories lives in exactly one place in code: `config.CATEGORIES` in `src/echo/config.py`. Nothing else should hardcode the taxonomy.

---

## Sentiment, urgency & consistency

- **Sentiment:** 3-class (positive / neutral / negative), scored on the *categorized aspect* so sentiment and category stay coherent on mixed feedback.
- **Urgency 1–5, anchored to business stakes** (hybrid LLM + deterministic floor):
  - **5** — fraud / payment taken but no order / safety hazard / mass checkout outage
  - **4** — individual money at stake or purchase blocked → **Urgent queue**
  - **3** — delayed order needing follow-up
  - **2** — minor dissatisfaction
  - **1** — praise / no action
  - *Deterministic floor:* a ticket containing signals like "fraud / double-charged / never arrived" is floored at 4, reducing the model's subjectivity on the highest-stakes items. In plain terms: even if the LLM itself would have scored something a 2, a hardcoded pattern match on high-stakes phrases forces the score up to at least a 4, so the model's judgment can never quietly under-rate the scariest cases.
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

Each theme carries: label, owner (majority category → team), **$ impact**, item count, and a representative quote. Vector clustering beats keyword search here — *"app crashes on login"* and *"sign-in keeps failing"* share no keywords but one meaning (an "embedding" is just a list of numbers that captures what a piece of text means, so texts with similar meaning end up with similar numbers even when they don't share a single word).

**Caveat to know before reading a theme's item count:** the LLM labeller only ever sees a handful of representative quotes from a cluster, not every member. So `item_count` is the whole cluster's size, but any specific detail mentioned in the label (a number, a exact product) reflects only the quotes the LLM happened to see — the prompt explicitly forbids stating a number unless it's shared across most shown quotes, precisely to prevent a label overgeneralizing from one example to the whole cluster.

---

## Money-weighting engine (the formulas, explained)

All money math is **SQL/Python — never the LLM.** This section spells out each formula in plain language alongside the math, since it's the part of the system most likely to confuse a new developer.

### Item impact — the ranking primitive

```
impact = severity_weight(urgency) × value × sentiment_mult
```

In plain words: every feedback item gets one "impact score" used to rank and sort. It's built from three multipliers:

- `severity_weight(urgency)` — how bad is this, on the 1–5 urgency scale? Mapped to a multiplier: urgency 1→5 = `{0.1, 0.3, 0.6, 1.0, 1.5}`. A level-5 urgency item counts 15× more than a level-1 item.
- `sentiment_mult` — how negative is the tone? `negative = 1.0`, `neutral = 0.4`, `positive = 0.1`. A negative complaint counts far more than a positive comment that happens to mention the same category.
- `value` — the dollar figure from the category's money mechanic (see the taxonomy table above). **If the dataset has no money field at all, `value = 1`**, and the whole formula cleanly collapses to *volume × severity* — echo degrades gracefully instead of crashing when money data is missing.

Impacts aggregate (sum) to **category impact** and **theme impact** — i.e. a category's or theme's total impact is just the sum of its items' impact scores.

### Two separate dollar figures — and why they're kept apart

Keeping these two numbers visibly separate *is* what makes the reported dollar figures credible — one is a fact, the other is an estimate, and conflating them would let a single "$X at stake" number quietly smuggle in a guess as if it were a certainty.

1. **Direct Exposure (actual, deterministic)** — real money that is already provably at stake, computed straight from stored fields: refund pending, disputed charge, lost/damaged order value, failed-checkout GMV. No modeling, no assumptions — this number could be handed to an auditor.

2. **Retention Risk (modeled, an estimate)** — the dollar value of customers who might churn (stop buying) because of this issue. Per at-risk customer:
   ```
   retention_risk = customer_value × churn_uplift × category_propensity
   ```
   In plain words: take what a customer is worth (`customer_value`), multiply by how much more likely they are to churn because of this specific bad experience (`churn_uplift`), multiply by how much this category of issue tends to drive that particular customer's churn (`category_propensity`).
   - `customer_value` = CLV (customer lifetime value) if we have it → else `AOV (average order value) × expected annual orders` → else a flat assumed value (by tier, see below).
   - **De-duplicated by customer** — one at-risk customer is counted once, attributed to their single worst issue, so the same person complaining three times doesn't triple-count.
   - **Guardrails:** every assumption behind this formula lives in one documented place in `config.py`; Retention Risk is always displayed *separately* from Direct Exposure and explicitly labeled **"modeled estimate"**; it's reported as a **low / base / high sensitivity range**, never one falsely-precise single number.

### Graceful degradation tiers

echo reports which tier the *current* data supports — it never pretends to have money data it doesn't:

| Tier | Data available | What echo can report |
|---|---|---|
| T0 | text only | Volume × severity ranking |
| T1 | + rating/NPS (review/survey) | Sentiment-weighted + silver-label validation |
| T2 | + order value / AOV | Direct Exposure in real $ |
| T3 | + refund amount / status / customer | Precise exposure + Retention Risk model |

**This is standard practice, not invention.** The method is textbook **Voice-of-Customer (VoC) / CX economics**. Direct Exposure is ordinary operational **cost-to-serve** (contact cost per ticket, refund/chargeback rates, cart-abandonment GMV). Retention Risk is the industry **"revenue at risk"** metric — `customers at risk × customer lifetime value (CLV/LTV) × churn probability` — the same model enterprise platforms (Qualtrics XM, Medallia) compute, often refined with **driver analysis** (which factors statistically predict churn). The one honest simplification: where those platforms calibrate `churn_uplift` from historical churn data (e.g. survival analysis), echo uses transparent, documented assumptions with a sensitivity range — the correct call absent longitudinal data, and defensible precisely because it's labeled as modeled.

The engine's code lives in `src/echo/money/engine.py` (queries + aggregation) and `src/echo/money/mechanics.py` (the per-category `value` formulas from the taxonomy table); reused everywhere a dollar figure is needed (summary, urgent queue, themes, user analytics) rather than reimplemented per feature.

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
| `GET /users/analytics` | Per-user submission behavior (User Analytics page) | COMPANY |

**Streamlit** dashboard — a **thin client** that talks only to the API (never to the DB or LLM directly), organized as a single router entrypoint (`app.py` + `st.navigation`) with the content pages under `views/`:
- **Landing / login screen** for signed-out visitors (no sidebar): the `echo` wordmark with animated side arc-waves, and a Login button that reveals login / create-account.
- **GEN-POP** sees a single **Feed a feedback** page with a top-right logout and no status chrome. The rating field adapts to the feedback type: a Review shows a **clickable 1–5 star widget** (click the Nth star to set the rating; a real e-commerce-style control, not a slider), a Survey shows a **0–10 recommend-score number input**, and a Support ticket carries no rating field at all. All dropdowns use plain-language labels; there are no sliders anywhere in the app — every previously-slider-based control (the review rating, survey NPS score, and the admin "how many items" limits on Urgent Queue / Ask echo) is now a star widget or a plain number input, so exact values are always visible rather than eyeballed off a track. On submit the customer sees only a thank-you confirmation (never the internal classification, sentiment, urgency, or money figures — those still run server-side for the company view), plus a **Submit another feedback** button that resets the form. Below the form is a read-only list of the customer's own past submissions (their text + date).
- **COMPANY** sees the analytics pages in the sidebar — Overview, Urgent Queue, Themes, Weekly Summary, Ask echo, Model Evaluation, **User Analytics** — plus a DB/LLM status box. Every chart has a title, axis labels, legend, and date range; volume by category & source, sentiment trend, urgent queue by $ exposure, top themes by revenue-at-risk, and source-sliced cross-tabs.
- **User Analytics** (COMPANY only): who is actually using echo and what are they saying. A roster of every registered account (email, role, submission count, overall opinion, average urgency, last active), then a per-user drill-down — sentiment split, category focus, an opinion-over-time trend, money at stake from that user's negative feedback (Direct Exposure + the modeled Retention Risk range, reusing the same money engine as everywhere else), and their own submission history with full classification detail (an admin view, unlike the customer-facing Feed a feedback page, which never shows classification). Scoped strictly to feedback tied to a real account (`submitter_id IS NOT NULL`) — the 15k batch corpus has no owner and never appears here.
- **UI house style:** no emojis and no em dashes in any user-facing string, and no sliders — every numeric input is a plain number box or, for the star rating, a clickable widget with the exact value always visible.

---

## Authentication & roles

echo has two kinds of user, stored in one `users` table:

- **GEN-POP** — end users who *feed a feedback*. Can submit (`POST /feedback`) and view **only the feedback they submitted**. Public sign-up (`POST /auth/register`) always creates this role.
- **COMPANY** — staff/admins who *see all feedback and analytics*. Full read access to every analytics endpoint, including **User Analytics** (above). Company accounts are **not** self-registerable; they are provisioned by staff via `python -m echo.auth create-user ... --role company` (or the `seed` command).

Mechanics: passwords are bcrypt-hashed; tokens are signed HS256 (PyJWT) with `JWT_SECRET`. The login route uses the OAuth2 password flow, so Swagger UI shows an **Authorize** button. The security boundary is server-side — the frontend only decides what to *show*, never what's *allowed*. `feedback.submitter_id` (a FK to `users.id`, NULL for the batch corpus) powers GEN-POP "view own".

**Sign-up:** `POST /auth/register` enforces a password policy (at least 8 characters, an uppercase letter, a lowercase letter, a number, and a special character — checked with a Pydantic validator on `RegisterIn`, so a bad password gets one clear `422` message naming everything missing) and **returns the created account, not a token** — registering no longer logs the user in. The frontend's sign-up form adds a client-side Confirm-password field (mismatch is caught before the request is even sent) and, on success, switches back to the login form with a one-time "Account created" banner. This policy only applies to public self-registration; `python -m echo.auth create-user`/`seed` are unaffected, so staff and demo accounts can use any password.

---

## Running echo locally (for developers)

Python 3.13. The `echo` package is run from `src/` (either `PYTHONPATH=src` or `pip install -e .`). `make help` lists every command below as a shorter `make` target — see the [Makefile reference](#makefile-reference).

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

# Or with the Makefile (identical commands, shorter to type):
make install && make db && make run-api   # in one terminal
make run-frontend                         # in another terminal
make dev                                  # or: both at once in one terminal, Ctrl-C stops both
```

**Every pipeline stage can also be run standalone** (useful for re-running just one step, e.g. after a prompt change):

```bash
PYTHONPATH=src .venv/bin/python -m echo.corpus              # full corpus build + verify
PYTHONPATH=src .venv/bin/python -m echo.corpus --limit 50   # tiny dry-run per source
PYTHONPATH=src .venv/bin/python -m echo.corpus --offline    # deterministic stub, no API calls
PYTHONPATH=src .venv/bin/python -m echo.corpus --stage econ|verify|all   # single stage
PYTHONPATH=src .venv/bin/python -m echo.db --keep           # append instead of truncate+reload
PYTHONPATH=src .venv/bin/python -m echo.classify            # classify all unclassified feedback
PYTHONPATH=src .venv/bin/python -m echo.embed               # embed all texted items with no embedding yet
PYTHONPATH=src .venv/bin/python -m echo.money               # money-engine report (all-time)
PYTHONPATH=src .venv/bin/python -m echo.money --week 2018-03-05     # one week
PYTHONPATH=src .venv/bin/python -m echo.themes --week 2018-03-05   # build themes for one week
PYTHONPATH=src .venv/bin/python -m echo.summary --week 2018-03-05  # generate the weekly summary
PYTHONPATH=src .venv/bin/python -m echo.rag "late deliveries?"     # ask echo one question from the CLI
PYTHONPATH=src .venv/bin/python -m echo.classify.evaluate          # gold-set + silver-sentiment report cards
PYTHONPATH=src .venv/bin/python -m echo.auth create-user --email x@y.com --password pw --role company  # one account
```

**Environment (`.env`, copied from `.env.example`):**
- `OPENAI_API_KEY` — only needed for the live LLM features (submit, generate summary, Ask echo); reads/analytics work without it.
- `ECHO_MODEL` — override the model name (default `gpt-4o-mini`).
- `ECHO_OFFLINE` — `true` uses a deterministic offline stub generator for corpus synthesis, no API calls.
- `DATABASE_URL` — defaults to a local server + a database named `echo` (user = your OS login); override for a different host/user/db.
- `JWT_SECRET` — **set this.** It defaults to an insecure dev value and warns at import; anything real must override it. Optional: `JWT_EXPIRE_MINUTES` (default 720, i.e. 12 hours).
- `SEED_COMPANY_EMAIL` / `SEED_COMPANY_PASSWORD` / `SEED_GENPOP_EMAIL` / `SEED_GENPOP_PASSWORD` — accounts created by `python -m echo.auth seed`. Defaults: `admin@echo.example` / `admin123` (company) and `user@echo.example` / `user123` (gen_pop). Change these before any real use.

**Auth CLI:** `python -m echo.auth seed | create-user --email ... --password ... --role {company,gen_pop} [--name ...] | list`.

**Lint & DB console:**
```bash
.venv/bin/ruff check src/     # lint (config in pyproject.toml; must be clean)
psql -h localhost -d echo     # DB console
```

> **Local vs. Docker (important):** local is the primary, day-to-day instance; Docker is only for periodically verifying the packaged deployment, then torn down — never run both at once. They collide on ports 8000/8501/5432 and are *separate databases*, so data submitted to one won't appear in the other. Run **`./scripts/status.sh`** (or `make status`) to see which instance owns each port before switching.

---

## Makefile reference

`make help` prints this list; it's the same commands as above, just shorter. The Makefile is a thin wrapper — it doesn't do anything a command in this README doesn't already do by hand.

| Command | What it does |
|---|---|
| `make install` | Create `.venv` and install every extra (`corpus,db,pipeline,app,frontend,dev`) |
| `make run-api` | Run the backend locally (uvicorn, `:8000`) |
| `make run-frontend` | Run the dashboard locally (streamlit, `:8501`) |
| `make dev` | Run backend + frontend together in one terminal; Ctrl-C stops both |
| `make status` | Which instance (local/docker/conflict/free) currently owns ports 5432/8000/8501 |
| `make docker-up` | `docker compose up --build` (packaged demo, needs Docker Desktop) |
| `make docker-down` | `docker compose down` |
| `make docker-build` | `docker compose build` (build images without starting containers) |
| `make docker-refresh` | Regenerate the Docker seed from local Postgres, rebuild, fully reset the stack |
| `make lint` | `ruff check src/` |
| `make test` | `pytest` (no tests exist yet — placeholder for when there are) |
| `make clean` | Remove `__pycache__` / `.ruff_cache` |
| `make corpus` | Full corpus build + verify (needs `data/raw/`, see `CORPUS.md`) |
| `make db` | (Re)load `data/processed` → Postgres |
| `make classify` | Classify all unclassified feedback |
| `make embed` | Embed all texted items with no embedding yet |
| `make money` | Print the money-engine report (all-time) |
| `make themes WEEK=2018-03-05` | Build themes for one week |
| `make summary WEEK=2018-03-05` | Generate the weekly summary for one week |
| `make ask Q="late deliveries?"` | Ask echo one question from the CLI |
| `make evaluate` | Gold-set confusion matrix + silver-sentiment-at-scale report |

---

## Docker packaging & image storage

**Target shape: exactly 2 app containers** — `backend` (FastAPI + the whole pipeline) and `frontend` (Streamlit) — plus **PostgreSQL with the pgvector extension** as an external data store (vectors live in Postgres, not a separate vector database).

```bash
docker compose up --build                                   # -> http://localhost:8501
docker compose exec backend python -m echo.auth seed        # the seed DB dump has no users; seed them
docker compose down                                          # tear down when finished
docker compose down -v                                       # also wipe the Postgres volume (forces a re-seed)
```

### What each container is

- **`db`** — `pgvector/pgvector:pg16` (official Postgres image + the pgvector extension baked in). On an **empty** volume, Postgres's own `docker-entrypoint-initdb.d` convention auto-runs everything in `docker/seed/` once: `00_extensions.sql` (`CREATE EXTENSION vector`) then `10_echo_seed.sql.gz` (a `pg_dump --exclude-table-data=embeddings` of the live local database — full schema + all data *except* embeddings rows, which are left out because generating them needs an `OPENAI_API_KEY` regardless, so shipping them for free would buy nothing). Published on `5432` for debugging (`psql -h localhost -d echo -U echo`).
- **`backend`** — built from `docker/backend.Dockerfile` (`python:3.13-slim`, installs the `corpus,db,pipeline,app` extras, entrypoint is `uvicorn echo.api.main:app` only — the corpus loader must never run against a seeded database). Talks to `db` over the internal Docker network at `db:5432`. Published on `8000`.
- **`frontend`** — built from `docker/frontend.Dockerfile` (installs the `frontend` extra only, runs `streamlit run app.py`). Talks to `backend` over the internal network at `http://backend:8000`. Published on `8501`.
- **`seed`** (opt-in, `--profile rebuild`) — regenerates `docker/seed/10_echo_seed.sql.gz` from whatever `db` is currently running, for when the pipeline tables change (e.g. a new prompt version was run and you want the packaged demo to ship the new results): `docker compose --profile rebuild run --rm seed`.

### Where the images live (GHCR)

On every push to `main` that touches `src/**`, `docker/**`, or `pyproject.toml` (or manually via the Actions tab), `.github/workflows/publish-images.yml` builds both images and pushes them to **GitHub Container Registry (GHCR)**:

```
ghcr.io/praveen-at-calfus/echo-backend:latest
ghcr.io/praveen-at-calfus/echo-backend:<commit-sha>
ghcr.io/praveen-at-calfus/echo-frontend:latest
ghcr.io/praveen-at-calfus/echo-frontend:<commit-sha>
```

Each image gets two tags: `:latest` (always the most recent `main`) and `:<commit-sha>` (an immutable, pinned reference to exactly that commit — pull this one if you need reproducibility, e.g. in a deployment manifest). The workflow uses the repo's built-in `GITHUB_TOKEN` (no personal access token needed) and a GitHub Actions build cache so repeat builds are fast. **GHCR packages default to private on first push regardless of repo visibility** — a one-time manual step (repo → Packages → package settings → Change visibility) is needed if these should be public.

This publishing step produces an *archived, distributable* image for deploying elsewhere; local day-to-day development still uses `docker compose up --build`, which builds from source and is unrelated to what's sitting in GHCR.

**Pulling a published image directly** (instead of building locally):
```bash
docker pull ghcr.io/praveen-at-calfus/echo-backend:latest
docker pull ghcr.io/praveen-at-calfus/echo-frontend:latest
```

### The port-collision gotcha

Local Postgres/API/frontend and the Docker `db`/`backend`/`frontend` containers default to the exact same host ports (5432/8000/8501). Whichever binds first wins, **silently** — the other side gets no error, it's just unreachable, and worse, they're two entirely separate databases even if both happen to be listening. Always run `./scripts/status.sh` (or `make status`) before assuming a stale page or "missing" data is a code bug — it reports `local` / `docker` / a genuine `conflict` / `free` per port.

**`./scripts/refresh-docker.sh`** (or `make docker-refresh`) automates the full "verify the packaged build still works" sequence: it refuses to run unless port 5432 is unambiguously local Postgres (so it can never dump stale Docker data back into the seed), then regenerates the seed from local Postgres, rebuilds the images, `down -v` (wipes the volume), `up -d`, and polls `/health` until the backend is ready.

---

## CI/CD: automated image publishing

`.github/workflows/publish-images.yml` is the only CI workflow in this repo today. What it does, in plain terms:

1. **Trigger:** runs automatically on every push to `main` that changes `src/**`, `docker/**`, or `pyproject.toml` — deliberately path-filtered so a docs-only or memory-only commit doesn't burn a rebuild. Can also be triggered manually from the Actions tab (`workflow_dispatch`) for any reason (e.g. re-publishing after a GHCR outage).
2. **Concurrency:** a new run for the same branch cancels any run already in progress for that branch, so pushes in quick succession don't queue up redundant builds.
3. **Build:** uses Docker Buildx to build both `docker/backend.Dockerfile` and `docker/frontend.Dockerfile` from the repo root as build context, with GitHub Actions' own layer cache (`cache-from`/`cache-to: type=gha`) so unchanged layers aren't rebuilt from scratch.
4. **Push:** logs into GHCR using the repo's automatic `GITHUB_TOKEN` (nothing to configure) and pushes both `:latest` and `:<commit-sha>` tags for each image.

This is the only automation gate in the repo right now — there's no separate "run tests" or "lint" CI job yet (`make lint` / `make test` are run locally). If you add one, mirror this workflow's path-filtering and concurrency-group pattern so it doesn't fire on every commit.

---

## Contributing: branches, commits & pull requests

This repo uses a standard feature-branch + pull-request workflow — nothing merges straight to `main`.

1. **Branch off `main`** for any change: `git checkout -b <short-descriptive-name>`.
2. **Commit locally** as you go; keep commits focused (one logical change per commit) and write messages that explain *why*, not just *what*.
3. **Push the branch** and open a pull request:
   ```bash
   git push -u origin <branch-name>
   gh pr create --title "..." --body "..."
   ```
   (or open the PR from the GitHub web UI — the "Compare & pull request" banner appears automatically after a push).
4. **The PR description should include a short summary of the change and a test plan** — what was actually run/verified (curl checks, `AppTest` runs, a local Docker rebuild, etc.), not just "should work."
5. **Review and merge.** Once approved, merge the PR (a merge commit is fine — this repo doesn't require squash/rebase). Merging into `main` with changes under `src/**`, `docker/**`, or `pyproject.toml` automatically triggers the [image-publishing workflow](#cicd-automated-image-publishing) above.
6. **After merging**, switch back to `main` and pull:
   ```bash
   git checkout main
   git pull
   ```
   The now-merged feature branch can be deleted locally (`git branch -d <branch-name>`) and on GitHub (the PR page offers a "Delete branch" button after merge).

Keep `CLAUDE.md`'s "Current state" section honest as you build — it's the living build log this README's design spec gets checked against.

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
| Packaging | Docker — **exactly 2 containers: `backend` + `frontend`**, published to GHCR (Postgres is an external/managed service, not shipped in an app image) |
| CI/CD | GitHub Actions — builds + publishes both Docker images to GHCR on every `main` push touching relevant paths |
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
