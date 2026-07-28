"""``python -m echo.rag "question"`` — ask echo one question from the command line."""

from __future__ import annotations

import argparse

from echo import config
from echo.rag.answer import ask


def main() -> int:
    ap = argparse.ArgumentParser(prog="echo.rag")
    ap.add_argument("question", help="the question to ask echo")
    ap.add_argument("--k", type=int, default=config.RAG_TOP_K, help="how many feedback items to retrieve")
    args = ap.parse_args()

    result = ask(args.question, k=args.k)
    print(f'\nQ: {result["question"]}\n')
    print(f'A: {result["answer"]}\n')
    if result["citations"]:
        print("Sources:")
        for c in result["citations"]:
            print(f'  [{c["item_id"]}] ({c["source_type"]}) {c["snippet"]!r}')
    if result["stats"]:
        s = result["stats"]
        print(f'\n{s["n_retrieved"]} items retrieved · sentiment {s["sentiment"]} · '
              f'top category: {s["top_category"]} ({s["top_category_count"]}) · '
              f'direct exposure R${s["direct_exposure"]:,.2f} · '
              f'retention(base) R${s["retention_base"]:,.2f} · '
              f'revenue at risk R${s["revenue_at_risk"]:,.2f}')
    if "est_cost" in result:
        print(f'\n(est. ${result["est_cost"]:.4f} · numbers are SQL-computed; the model only wrote prose)')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
