#!/usr/bin/env python3
"""Classify every pulled contract and write the labels to disk.

One row per distinct contract number, using the largest option period's title.
Labels land in ``data/contract_classifications.csv``; the underlying LLM
responses are cached in ``data/classifications.json`` so re-running is free.

    uv run python analysis/classify_contracts.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dcprocure.classify import DEFAULT_MODEL, classify_contracts  # noqa: E402
from dcprocure.parse import load_contracts  # noqa: E402

OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "contract_classifications.csv"


def main(model: str = DEFAULT_MODEL) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    contracts = load_contracts()

    # One row per contract, carrying its lifetime ceiling and NIGP classes.
    by_contract = contracts.groupby("contract_number").agg(
        title=("title", "first"),
        vendor=("vendor", "first"),
        agency=("agency", "first"),
        ceiling=("amount", "sum"),
        option_periods=("id", "size"),
        contract_type=("contract_type", "first"),
        primary_group=("primary_group", "first"),
    )
    by_contract["classes"] = (
        contracts.groupby("contract_number")["nigp_classes"]
        .agg(lambda s: sorted({c for row in s for c in row}))
    )

    records = [
        {
            "title": row.title,
            "vendor": row.vendor,
            "agency": row.agency,
            "amount": f"${row.ceiling:,.0f}",
            "classes": row.classes,
        }
        for row in by_contract.itertuples()
    ]
    print(f"classifying {len(records):,} contracts with {model}")

    predictions = classify_contracts(records, model=model)
    labelled = by_contract.reset_index()
    for field in ("work_type", "buys_people_not_deliverables", "system_name",
                  "confidence", "is_technology"):
        labelled[field] = [p.get(field) if p else None for p in predictions]

    failed = labelled["work_type"].isna().sum()
    if failed:
        print(f"warning: {failed} contracts could not be classified")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    labelled.to_csv(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH} ({len(labelled):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
