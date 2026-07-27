"""Typed readers for the 7 Olist tables this step needs.

A *real* CSV parser is mandatory: 3,834 review comments contain embedded
newlines and many contain embedded commas inside quoted fields, so any
line-splitting approach is wrong. pandas' C parser reads exactly 99,224
review rows (40,950 with text).
"""

from __future__ import annotations

import pandas as pd

from echo import config

_TS_COLS = {
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
    "review_creation_date",
    "review_answer_timestamp",
    "shipping_limit_date",
}


def _path(name: str):
    return config.RAW_DIR / config.RAW_FILES[name]


def _read(name: str, usecols: list[str] | None = None) -> pd.DataFrame:
    df = pd.read_csv(
        _path(name),
        usecols=usecols,
        dtype=str,  # read everything as string; cast explicitly below
        keep_default_na=False,  # empty review comment -> "" not NaN
        na_values=[],
        encoding="utf-8-sig",
    )
    for col in df.columns:
        if col in _TS_COLS:
            df[col] = pd.to_datetime(df[col].replace("", pd.NA), errors="coerce")
    return df


def read_orders() -> pd.DataFrame:
    return _read("orders")


def read_reviews() -> pd.DataFrame:
    df = _read("reviews")
    df["review_score"] = pd.to_numeric(df["review_score"], errors="coerce").astype("Int64")
    return df


def read_order_items() -> pd.DataFrame:
    df = _read("order_items")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["freight_value"] = pd.to_numeric(df["freight_value"], errors="coerce")
    return df


def read_order_payments() -> pd.DataFrame:
    df = _read("order_payments")
    df["payment_value"] = pd.to_numeric(df["payment_value"], errors="coerce")
    df["payment_installments"] = pd.to_numeric(
        df["payment_installments"], errors="coerce"
    ).astype("Int64")
    return df


def read_customers() -> pd.DataFrame:
    return _read("customers")


def read_products() -> pd.DataFrame:
    return _read("products", usecols=["product_id", "product_category_name"])


def read_category_translation() -> pd.DataFrame:
    return _read("category_translation")
