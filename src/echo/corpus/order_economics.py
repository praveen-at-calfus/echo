"""The order-economics reference view — one row per ``order_id``.

This is the shared source of truth backing both synthesis grounding and the
later money engine. It embodies the anti-hallucination invariant: every
money/date number the LLM later phrases originates *here*, computed in pandas,
never invented. Aggregation rules across the one-to-many child tables are
deterministic and documented inline.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from echo import config
from echo.corpus import csv_io

# Percentile guardrails (README-measured) — a gross deviation means the join broke.
_ORDER_VALUE_CHECKS = {"median": (95, 115), "p95": (400, 500), "max": (13000, 14000)}


def _items_agg() -> pd.DataFrame:
    """Aggregate order_items to one row per order: total product value, total freight, item count, and the English category name of the dominant (highest-price) product."""
    items = csv_io.read_order_items()
    agg = items.groupby("order_id").agg(
        product_value=("price", "sum"),
        freight_value=("freight_value", "sum"),
        item_count=("order_id", "size"),
    )
    # Dominant product = highest-price line item (tie-break: product_id asc).
    top = (
        items.sort_values(["order_id", "price", "product_id"], ascending=[True, False, True])
        .drop_duplicates("order_id", keep="first")
        .set_index("order_id")["product_id"]
    )
    products = csv_io.read_products()
    trans = csv_io.read_category_translation()
    id_to_en = (
        products.merge(trans, on="product_category_name", how="left")
        .set_index("product_id")["product_category_name_english"]
    )
    agg["product_category_en"] = top.map(id_to_en)
    agg["product_category_en"] = agg["product_category_en"].replace("", np.nan).fillna("unknown")
    return agg.reset_index()


def _payments_agg() -> pd.DataFrame:
    """Aggregate order_payments to one row per order: total payment value, whether multiple payment types were used, and the dominant (highest-value) payment type and installment count."""
    pay = csv_io.read_order_payments()
    agg = pay.groupby("order_id").agg(
        payment_value_total=("payment_value", "sum"),
        n_payment_types=("payment_type", "nunique"),
    )
    agg["payment_mixed"] = (agg["n_payment_types"] > 1).astype("boolean")
    top = (
        pay.sort_values(["order_id", "payment_value", "payment_type"], ascending=[True, False, True])
        .drop_duplicates("order_id", keep="first")
        .set_index("order_id")
    )
    agg["payment_type"] = top["payment_type"]
    agg["payment_installments"] = top["payment_installments"]
    return agg.drop(columns="n_payment_types").reset_index()


def _reviews_agg() -> pd.DataFrame:
    """Aggregate reviews to one row per order: the lowest review score (if an order has more than one review) and whether any review has actual text."""
    rev = csv_io.read_reviews()
    rev["has_text"] = rev["review_comment_message"].str.strip().ne("")
    return (
        rev.groupby("order_id")
        .agg(review_score=("review_score", "min"), has_text_review=("has_text", "max"))
        .reset_index()
    )


def build_order_economics() -> pd.DataFrame:
    """Join orders, customers, items, payments, and reviews into one row per order with derived money/fulfillment fields, and return the resulting reference DataFrame."""
    orders = csv_io.read_orders()
    customers = csv_io.read_customers()

    df = orders.merge(customers, on="customer_id", how="left")
    df = df.merge(_items_agg(), on="order_id", how="left")
    df = df.merge(_payments_agg(), on="order_id", how="left")
    df = df.merge(_reviews_agg(), on="order_id", how="left")

    df["order_value"] = df["product_value"] + df["freight_value"]  # NaN if no line items

    df = df.rename(
        columns={
            "order_purchase_timestamp": "purchase_ts",
            "order_approved_at": "approved_ts",
            "order_delivered_carrier_date": "carrier_ts",
            "order_delivered_customer_date": "delivered_ts",
            "order_estimated_delivery_date": "estimated_ts",
            "customer_zip_code_prefix": "customer_zip_prefix",
        }
    )

    df["delivery_days"] = (df["delivered_ts"] - df["purchase_ts"]).dt.days
    df["lateness_days"] = (df["delivered_ts"] - df["estimated_ts"]).dt.days
    df["delivered_late"] = (df["lateness_days"] > 0).where(df["lateness_days"].notna()).astype("boolean")

    # Classify each order into exactly one outcome bucket by checking these conditions in
    # order (first match wins); anything that matches none of them falls back to "other".
    st, late = df["order_status"], df["lateness_days"]
    df["fulfillment_outcome"] = np.select(
        [
            st.eq("canceled"),
            st.eq("unavailable"),
            st.eq("shipped"),
            st.eq("delivered") & late.gt(0),
            st.eq("delivered") & late.le(0),
        ],
        ["canceled", "unavailable", "shipped_not_delivered", "late_delivered", "on_time_delivered"],
        default="other",
    )
    # Refunds aren't native — inferred from canceled orders (README).
    df["refund_amount_proxy"] = df["payment_value_total"].where(st.eq("canceled"))
    df["has_text_review"] = df["has_text_review"].fillna(False).astype("boolean")

    cols = [
        "order_id", "customer_id", "customer_unique_id",
        "customer_state", "customer_city", "customer_zip_prefix",
        "order_status", "fulfillment_outcome",
        "product_value", "freight_value", "order_value", "item_count", "product_category_en",
        "payment_value_total", "payment_type", "payment_installments", "payment_mixed",
        "refund_amount_proxy",
        "purchase_ts", "approved_ts", "carrier_ts", "delivered_ts", "estimated_ts",
        "delivery_days", "lateness_days", "delivered_late",
        "review_score", "has_text_review",
    ]
    return df[cols]


def summarize(df: pd.DataFrame) -> dict:
    """Compute summary statistics (order value percentiles, fulfillment/payment breakdowns, null rates) over the order-economics DataFrame, and return them as a dict."""
    ov = df["order_value"].dropna()
    return {
        "rows": int(len(df)),
        "order_value": {
            "count": int(ov.size),
            "median": round(float(ov.median()), 2),
            "p90": round(float(ov.quantile(0.90)), 2),
            "p95": round(float(ov.quantile(0.95)), 2),
            "max": round(float(ov.max()), 2),
            "mean": round(float(ov.mean()), 2),
        },
        "fulfillment_outcome": df["fulfillment_outcome"].value_counts().to_dict(),
        "payment_type": df["payment_type"].value_counts(dropna=True).to_dict(),
        "null_rate": {
            "order_value": round(float(df["order_value"].isna().mean()), 4),
            "product_category_en": round(float(df["product_category_en"].isna().mean()), 4),
            "review_score": round(float(df["review_score"].isna().mean()), 4),
        },
    }


def _self_check(stats: dict) -> None:
    """Raise a ValueError if any order_value statistic falls outside its expected range, signalling that a join with the raw CSVs likely broke."""
    ov = stats["order_value"]
    for key, (lo, hi) in _ORDER_VALUE_CHECKS.items():
        if not (lo <= ov[key] <= hi):
            raise ValueError(
                f"order_value {key}={ov[key]} outside expected [{lo},{hi}] — join likely broken"
            )


def build(force: bool = False) -> pd.DataFrame:
    """Return the cached order-economics parquet if it already exists (unless force is set), otherwise rebuild it from the raw CSVs, validate it, and write it to disk before returning it."""
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    if config.ORDER_ECONOMICS_PARQUET.exists() and not force:
        return pd.read_parquet(config.ORDER_ECONOMICS_PARQUET)

    df = build_order_economics()
    stats = summarize(df)
    _self_check(stats)

    df.to_parquet(config.ORDER_ECONOMICS_PARQUET, index=False)
    config.ORDER_ECONOMICS_STATS.write_text(json.dumps(stats, indent=2, default=str))
    return df


if __name__ == "__main__":
    d = build(force=True)
    print(f"order_economics: {len(d)} orders -> {config.ORDER_ECONOMICS_PARQUET}")
    print(json.dumps(summarize(d), indent=2, default=str))
