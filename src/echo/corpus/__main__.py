"""Corpus-builder orchestrator.

    python -m echo.corpus                 # full 5k/5k/5k build + verify
    python -m echo.corpus --limit 50      # tiny dry-run per source
    python -m echo.corpus --offline       # force the offline stub generator
    python -m echo.corpus --stage econ    # just (re)build order_economics
    python -m echo.corpus --stage verify  # just verify an existing build

The review + order-economics stages need no API key. Ticket/survey synthesis
uses OpenAI when ``OPENAI_API_KEY`` is set, else the offline stub.
"""

from __future__ import annotations

import argparse

from echo import config
from echo.corpus import (
    gold,
    manifest,
    order_economics,
    sample_reviews,
    synth_common,
    synth_surveys,
    synth_tickets,
    utils,
    verify,
)
from echo.schemas import envelope


def main() -> int:
    ap = argparse.ArgumentParser(prog="echo.corpus")
    ap.add_argument("--force", action="store_true", help="rebuild order_economics from CSVs")
    ap.add_argument("--limit", type=int, default=None, help="items per source (dry-run)")
    ap.add_argument("--offline", action="store_true", help="force offline stub generation")
    ap.add_argument("--stage", choices=["all", "econ", "verify"], default="all")
    args = ap.parse_args()

    if args.offline:
        config.settings.offline = True

    if args.stage == "verify":
        return verify.main()

    print(f"[1/8] order_economics (force={args.force}) ...")
    econ = order_economics.build(force=args.force)
    print(f"      {len(econ)} orders")
    if args.stage == "econ":
        return 0

    generator = synth_common.make_generator()
    guard = synth_common.DiversityGuard()
    build_id = manifest.corpus_build_id(generator.model)
    print(f"      corpus_build_id={build_id} · generator={generator.model}")

    print("[2/8] sampling reviews (MCMC) ...")
    reviews, rstats = sample_reviews.build_reviews(econ, limit=args.limit, build_id=build_id)
    print(f"      {len(reviews)} reviews · rmse/cell={rstats['mcmc_rmse_per_cell']}")

    if not config.settings.use_offline:
        print("[2.5] pre-warming generation cache (concurrent LLM calls) ...")
        rec = synth_common.RecordingGenerator(generator.model)
        synth_tickets.build_tickets(econ, rec, synth_common.DiversityGuard(), args.limit, build_id)
        synth_surveys.build_surveys(econ, rec, synth_common.DiversityGuard(), args.limit, build_id)
        print(f"      warming {len(rec.requests)} requests with {config.GEN_WORKERS} workers ...")
        print(f"      {synth_common.warm_cache(generator, rec.requests)}")

    print("[3/8] synthesizing tickets ...")
    tickets, tstats = synth_tickets.build_tickets(econ, generator, guard, args.limit, build_id)
    print(f"      {len(tickets)} tickets")

    print("[4/8] synthesizing surveys ...")
    surveys, sstats = synth_surveys.build_surveys(econ, generator, guard, args.limit, build_id)
    print(f"      {len(surveys)} surveys")

    all_items = reviews + tickets + surveys

    print("[5/8] writing JSONL shards + schema ...")
    utils.write_jsonl(config.PROCESSED_DIR / "reviews.jsonl", reviews)
    utils.write_jsonl(config.PROCESSED_DIR / "tickets.jsonl", tickets)
    utils.write_jsonl(config.PROCESSED_DIR / "surveys.jsonl", surveys)
    utils.write_jsonl(config.PROCESSED_DIR / "corpus.jsonl", all_items)
    envelope.export_json_schema(config.PROCESSED_DIR / "corpus_item.schema.json")

    print("[6/8] exporting gold candidates ...")
    gstats = gold.write_gold(all_items)
    print(f"      {gstats['candidates']} candidates ({gstats['by_source']})")

    print("[7/8] writing manifest ...")
    stage_stats = {
        "reviews": rstats,
        "tickets": tstats,
        "surveys": sstats,
        "gold": gstats,
        "guard_residual": guard.residual_near_dupes,
    }
    m = manifest.build_manifest(all_items, stage_stats, generator, build_id)
    manifest.write_manifest(m)

    print("[8/8] verifying ...")
    return verify.main()


if __name__ == "__main__":
    raise SystemExit(main())
