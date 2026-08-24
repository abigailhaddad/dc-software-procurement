#!/usr/bin/env python3
"""Evaluate delivery-structure classifications against hand-read gold labels.

Every label in ``delivery_gold_labels.csv`` was set by reading the contract text
directly, not by confirming the model's own output -- a test built from the
model's answers cannot fail.

The cases are weighted toward the distinction that broke a first attempt at this
analysis. Counting agile keywords flagged 28 contracts; hand-checking the 13 that
were also structurally waterfall found 5 false positives, three of them the same
"Experience in Agile methodology such as Scrum, Kanban" boilerplate sitting in
*staff qualifications*. Removing them moved the headline from p=0.01 to p=0.20.
So the fixture deliberately contains those five alongside six genuine delivery
commitments: a classifier that cannot separate them will score badly here.

    uv run python tests/eval_delivery.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.classify import DEFAULT_MODEL, classify_contracts  # noqa: E402
from dcprocure.documents import (  # noqa: E402
    agile_context_windows,
    delivery_excerpt,
    document_text,
    pick_award_document,
)

GOLD = Path(__file__).with_name("delivery_gold_labels.csv")
CONFIG = ROOT / "delivery_config.yaml"


def build_records(gold: pd.DataFrame) -> list[dict[str, str]]:
    """Rebuild each case's excerpt from the cached contract text."""
    details = {
        d["contractNumber"]: d
        for d in json.loads(
            (ROOT / "data" / "raw" / "contract_details.json").read_text(encoding="utf-8")
        )
    }
    records = []
    for row in gold.itertuples():
        detail = details[row.contract_number]
        text = document_text(None, pick_award_document(detail)["id"])
        excerpt = delivery_excerpt(text)
        context = agile_context_windows(text)
        if context:
            excerpt = f"{excerpt}\n\n=== passages mentioning agile terms ===\n{context}"
        records.append(
            {
                "title": detail.get("title"),
                "vendor": detail.get("vendor"),
                "excerpt": excerpt,
            }
        )
    return records


def evaluate(model: str = DEFAULT_MODEL) -> int:
    gold = pd.read_csv(GOLD)
    predictions = classify_contracts(
        build_records(gold), model=model, config_path=CONFIG
    )

    rows = []
    for row, prediction in zip(gold.itertuples(), predictions):
        got_structure = (prediction or {}).get("delivery_structure", "CALL_FAILED")
        got_mention = (prediction or {}).get("agile_mention_kind", "CALL_FAILED")
        rows.append(
            {
                "contract": row.contract_number,
                "structure_ok": got_structure in str(row.delivery_structure).split("|"),
                "mention_ok": got_mention in str(row.agile_mention_kind).split("|"),
                "got_structure": got_structure,
                "want_structure": row.delivery_structure,
                "got_mention": got_mention,
                "want_mention": row.agile_mention_kind,
                "notes": row.notes,
            }
        )
    results = pd.DataFrame(rows)
    results["correct"] = results.structure_ok & results.mention_ok

    n = len(results)
    print(f"Model: {model}")
    print(f"Overall:            {results.correct.sum()}/{n} ({100*results.correct.mean():.0f}%)")
    print(f"delivery_structure: {results.structure_ok.sum()}/{n}")
    print(f"agile_mention_kind: {results.mention_ok.sum()}/{n}")

    # The decisive question: does it separate a commitment from a mention?
    real = results[results.want_mention == "delivery_commitment"]
    traps = {"personnel_qualification", "pricing_unit", "unrelated_usage"}
    fake = results[results.want_mention.apply(
        lambda w: bool(traps & set(str(w).split("|")))
    )]
    print(f"\ngenuine agile commitments identified: {real.mention_ok.sum()}/{len(real)}")
    print(f"false-positive traps rejected:        {fake.mention_ok.sum()}/{len(fake)}")

    errors = results[~results.correct]
    if len(errors):
        print(f"\nERRORS ({len(errors)}):")
        for e in errors.itertuples():
            print(f"  {e.contract}")
            if not e.structure_ok:
                print(f"     structure: got {e.got_structure} | want {e.want_structure}")
            if not e.mention_ok:
                print(f"     mention:   got {e.got_mention} | want {e.want_mention}")
            print(f"     why: {e.notes[:120]}")
    else:
        print("\nNo errors.")
    return 0 if errors.empty else 1


if __name__ == "__main__":
    raise SystemExit(evaluate(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
