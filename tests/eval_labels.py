#!/usr/bin/env python3
"""Evaluate LLM contract classifications against gold labels.

The cases in ``gold_labels.csv`` are the ones commodity codes get wrong in both
directions: Medicaid managed care and food service sitting in the consulting
class, a plumbing contractor sitting in the software class, IT staff
augmentation filed under miscellaneous services. Each row carries a note saying
why it is there, so a config edit that breaks one is readable rather than
mysterious.

Where two labels are genuinely defensible the gold column holds both, separated
by a pipe -- the point is to catch real errors, not to punish the model for
resolving an ambiguity differently than a human would.

    uv run python tests/eval_labels.py [model]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dcprocure.classify import DEFAULT_MODEL, classify_contracts  # noqa: E402

GOLD_PATH = Path(__file__).with_name("gold_labels.csv")


def evaluate(model: str = DEFAULT_MODEL, gold_file: Path = GOLD_PATH) -> int:
    gold = pd.read_csv(gold_file)
    records = [
        {
            "title": row.title,
            "vendor": row.vendor,
            "agency": row.agency,
            "amount": row.amount,
            "classes": str(row.classes).split("|"),
        }
        for row in gold.itertuples()
    ]
    predictions = classify_contracts(records, model=model)

    rows = []
    for row, prediction in zip(gold.itertuples(), predictions):
        if prediction is None:
            rows.append({"id": row.id, "title": row.title, "llm_work_type": "CALL_FAILED",
                         "gold_work_type": row.work_type, "work_type_ok": False,
                         "llm_people": "", "gold_people": row.buys_people,
                         "people_ok": False, "notes": row.notes})
            continue
        accepted = set(str(row.work_type).split("|"))
        llm_people = str(prediction["buys_people_not_deliverables"])
        rows.append(
            {
                "id": row.id,
                "title": row.title,
                "llm_work_type": prediction["work_type"],
                "gold_work_type": row.work_type,
                "work_type_ok": prediction["work_type"] in accepted,
                "llm_people": llm_people,
                "gold_people": row.buys_people,
                "people_ok": llm_people == str(row.buys_people),
                "notes": row.notes,
            }
        )

    results = pd.DataFrame(rows)
    results["correct"] = results["work_type_ok"] & results["people_ok"]

    total = len(results)
    print(f"Model: {model}")
    print(f"Overall:   {results.correct.sum()}/{total} cases fully correct "
          f"({100 * results.correct.mean():.0f}%)")
    print(f"work_type: {results.work_type_ok.sum()}/{total} "
          f"({100 * results.work_type_ok.mean():.0f}%)")
    print(f"buys_people: {results.people_ok.sum()}/{total} "
          f"({100 * results.people_ok.mean():.0f}%)")

    errors = results[~results.correct]
    if len(errors):
        print(f"\nERRORS ({len(errors)}):")
        for e in errors.itertuples():
            print(f"  [{e.id}] {e.title[:58]}")
            if not e.work_type_ok:
                print(f"        work_type:   LLM={e.llm_work_type} | expected={e.gold_work_type}")
            if not e.people_ok:
                print(f"        buys_people: LLM={e.llm_people} | expected={e.gold_people}")
            print(f"        why: {e.notes}")
    else:
        print("\nNo errors.")
    return 0 if len(errors) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(evaluate(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
