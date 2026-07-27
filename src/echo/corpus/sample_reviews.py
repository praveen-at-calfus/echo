"""Review sampling (5k) via Metropolis-Hastings subset selection.

Draws a 5,000-review subset whose *joint* distribution over
``score x product-category-group x text-length-band`` matches a target
(default: the real population's empirical joint). Plain random sampling matches
the target only in expectation and with sampling variance; MH selection drives
the realized joint histogram tight to the target across all three axes at once,
reproducibly (fixed seed). The target is a config knob (``MCMC_TARGET``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from echo import config
from echo.corpus import csv_io, utils
from echo.schemas.envelope import CorpusItem, MessyFlags, Provenance, make_item_id

_N_TOP_CATEGORIES = 12


def _length_band(n: int) -> str:
    lo, hi = config.TEXT_LENGTH_BANDS
    return "short" if n < lo else ("med" if n <= hi else "long")


def _build_population(econ: pd.DataFrame) -> pd.DataFrame:
    """Text-bearing reviews joined to order economics, with cell features."""
    rev = csv_io.read_reviews()
    rev["text"] = rev["review_comment_message"].str.strip()
    rev = rev[rev["text"].str.len() >= 5].copy()  # Stage-0 too-short reject (rare here)
    rev = rev[rev["review_score"].notna()].copy()

    econ_cols = [
        "order_id", "customer_id", "customer_unique_id", "order_value", "freight_value",
        "refund_amount_proxy", "payment_type", "product_category_en", "order_status",
        "fulfillment_outcome", "lateness_days",
    ]
    pop = rev.merge(econ[econ_cols], on="order_id", how="left")

    pop["length"] = pop["text"].str.len()
    pop["length_band"] = pop["length"].map(_length_band)
    pop["category_group"] = pop["product_category_en"].fillna("unknown")

    top = pop["category_group"].value_counts().head(_N_TOP_CATEGORIES).index
    pop["category_group"] = pop["category_group"].where(
        pop["category_group"].isin(top), other="other"
    )
    pop["cell"] = (
        pop["review_score"].astype(int).astype(str)
        + "|" + pop["category_group"]
        + "|" + pop["length_band"]
    )
    return pop.reset_index(drop=True)


def _target_counts(pop: pd.DataFrame, cell_ids: np.ndarray, n_cells: int, n_sample: int) -> np.ndarray:
    """Expected per-cell counts in a perfect n_sample-sized draw from the target."""
    pop_counts = np.bincount(cell_ids, minlength=n_cells).astype(float)
    if config.MCMC_TARGET == "balanced":
        # Flatten across score bands while keeping category/length structure:
        # reweight each cell by 1/(share of its score band).
        scores = np.array([int(c.split("|")[0]) for c in _cell_labels], dtype=int)
        weights = np.ones(n_cells)
        for s in range(1, 6):
            mask = scores == s
            band = pop_counts[mask].sum()
            if band > 0:
                weights[mask] = 1.0 / band
        target = pop_counts * weights
    else:  # "representative"
        target = pop_counts
    target = target / target.sum() * n_sample
    return target


_cell_labels: list[str] = []  # populated in build_reviews for _target_counts


def _mcmc_select(cell_ids: np.ndarray, target: np.ndarray, n_sample: int, seed: int):
    rng = np.random.default_rng(seed)
    n_pop = cell_ids.size
    n_cells = target.size

    order = rng.permutation(n_pop)
    in_arr = order[:n_sample].copy()
    out_arr = order[n_sample:].copy()

    counts = np.bincount(cell_ids[in_arr], minlength=n_cells).astype(float)
    energy = float(np.sum((counts - target) ** 2))

    iters = config.MCMC_ITERS
    t0, tmin = config.MCMC_T0, config.MCMC_T_MIN
    anneal = (tmin / t0) ** (1.0 / iters)
    temp = t0

    ui, uj, uacc = rng.integers(0, n_sample, iters), rng.integers(0, out_arr.size, iters), rng.random(iters)
    for k in range(iters):
        pi, pj = int(ui[k]), int(uj[k])
        i, j = int(in_arr[pi]), int(out_arr[pj])
        ca, cb = cell_ids[i], cell_ids[j]
        if ca != cb:
            d = (
                (counts[ca] - 1 - target[ca]) ** 2 - (counts[ca] - target[ca]) ** 2
                + (counts[cb] + 1 - target[cb]) ** 2 - (counts[cb] - target[cb]) ** 2
            )
            if d <= 0 or uacc[k] < np.exp(-d / temp):
                counts[ca] -= 1
                counts[cb] += 1
                energy += d
                in_arr[pi], out_arr[pj] = j, i
        temp *= anneal

    return in_arr, counts, energy


def build_reviews(econ: pd.DataFrame, limit: int | None = None, build_id: str | None = None):
    """Return (list[CorpusItem], stats dict)."""
    global _cell_labels
    pop = _build_population(econ)

    cats = pd.Categorical(pop["cell"])
    _cell_labels = list(cats.categories)
    cell_ids = cats.codes.astype(np.int64)
    n_cells = len(_cell_labels)

    n_sample = min(config.N_REVIEWS if limit is None else limit, len(pop))
    target = _target_counts(pop, cell_ids, n_cells, n_sample)
    sel_idx, counts, energy = _mcmc_select(
        cell_ids, target, n_sample, utils.child_seed(config.SEED, "reviews")
    )

    sel = pop.iloc[np.sort(sel_idx)].reset_index(drop=True)

    items: list[CorpusItem] = []
    seen: dict[str, str] = {}
    for row in sel.itertuples(index=False):
        score = int(row.review_score)
        silver = "negative" if score <= 2 else ("positive" if score >= 4 else None)
        item_id = make_item_id("review", f"{row.review_id}:{row.order_id}")

        norm = utils.normalize_text(row.text)
        dup_of = seen.get(norm)
        if dup_of is None:
            seen[norm] = item_id

        created = row.review_creation_date
        created = created.to_pydatetime() if isinstance(created, pd.Timestamp) else created

        items.append(
            CorpusItem(
                item_id=item_id,
                source_type="review",
                source_score=float(score),
                source_scale="star_1_5",
                text=row.text,
                created_at=created,
                language="pt",
                order_id=row.order_id,
                customer_id=_s(row.customer_id),
                customer_unique_id=_s(row.customer_unique_id),
                order_value=_f(row.order_value),
                refund_amount=_f(row.refund_amount_proxy),
                freight_value=_f(row.freight_value),
                payment_type=_s(row.payment_type),
                product_category_en=_s(row.product_category_en),
                order_status=_s(row.order_status),
                fulfillment_outcome=_s(row.fulfillment_outcome),
                lateness_days=_f(row.lateness_days),
                silver_label=silver,
                silver_label_source="review_score" if silver else None,
                provenance=Provenance(
                    synthetic=False, grounded_on=row.review_id, corpus_build_id=build_id
                ),
                messy=MessyFlags(duplicate_of=dup_of),
            )
        )

    stats = {
        "population": int(len(pop)),
        "sampled": int(len(items)),
        "target": config.MCMC_TARGET,
        "mcmc_energy_final": round(energy, 3),
        "mcmc_rmse_per_cell": round(float(np.sqrt(energy / n_cells)), 4),
        "n_cells": n_cells,
        "score_dist_population": _prop(pop["review_score"].astype(int)),
        "score_dist_sampled": _prop(sel["review_score"].astype(int)),
        "duplicates_flagged": int(sum(1 for it in items if it.messy.duplicate_of)),
    }
    return items, stats


def _prop(s: pd.Series) -> dict:
    vc = s.value_counts(normalize=True).sort_index()
    return {int(k): round(float(v), 4) for k, v in vc.items()}


def _f(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v) else float(v)


def _s(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
