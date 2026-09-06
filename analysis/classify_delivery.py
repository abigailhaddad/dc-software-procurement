#!/usr/bin/env python3
"""Read delivery structure out of contract award PDFs.

Answers a question the structured fields cannot: is the work organized as a
sequential, gated, scope-frozen chain of deliverables (waterfall), or as
capacity to be directed as priorities change? And, separately, does the document
use agile vocabulary?

Keeping those two axes apart is the point. A contract that says "sprint" and
"product owner" while freezing scope and paying on accepted document
deliverables is waterfall wearing agile clothes, and that only shows up if
structure and vocabulary are never allowed to imply one another.

Agile vocabulary is detected twice over: once by regex, which is auditable and
model-free, and once by the LLM. Disagreement between them is worth looking at.

Two things can be wrong with the document before the classifier ever sees it:
the extraction can be mojibake that is long enough to pass the length check,
and the portal can have attached another contract's PDF. Both are checked here
and written to the output as ``excerpt_readable_share`` and
``names_other_contract``. Neither drops a row. Dropping would move published
counts that analysis/verify_claims.py pins, and re-judging a published label is
a deliberate act; the columns say which rows to exclude when someone restates
the baseline.

    uv run python analysis/classify_delivery.py
"""

from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.classify import DEFAULT_MODEL, classify_contracts  # noqa: E402
from dcprocure.client import DCProcurementClient  # noqa: E402
from dcprocure.documents import (  # noqa: E402
    MIN_TEXT_CHARS,
    agile_context_windows,
    agile_terms_used,
    delivery_excerpt,
    document_text,
    looks_unreadable,
    other_contracts_named,
    pick_award_document,
    readable_share,
)

DELIVERY_CONFIG = ROOT / "delivery_config.yaml"
OUT_PATH = ROOT / "data" / "delivery_structure.csv"

# Delivery methodology is only a meaningful question where something is built or
# run. Licence resale and pure staffing have no delivery structure to find.
BUILD_WORK_TYPES = {
    "custom_software_build",
    "cots_implementation",
    "software_om_support",
    "it_consulting_advisory",
}


def gather_excerpts(labels: pd.DataFrame, workers: int = 8) -> pd.DataFrame:
    """Fetch and cache the text of each award document, then pull out the
    sections that reveal delivery structure.

    Text is cached gzipped by :func:`dcprocure.documents.document_text`, so this
    is cheap on a second run and the PDFs themselves are never kept.
    """
    details = {
        d["contractNumber"]: d
        for d in json.loads(
            (ROOT / "data" / "raw" / "contract_details.json").read_text(encoding="utf-8")
        )
    }

    targets = []
    for label in labels.itertuples():
        detail = details.get(label.contract_number)
        if detail is None:
            continue
        document = pick_award_document(detail)
        if document is not None:
            targets.append((label.contract_number, document["id"]))
    print(f"{len(targets):,} contracts have an award document", flush=True)

    def read(target: tuple[str, str]) -> dict[str, Any] | None:
        contract_number, document_id = target
        try:
            text = document_text(client, document_id)
        except Exception as exc:
            logging.warning("unreadable %s: %s", contract_number, exc)
            return None
        if len(text) < MIN_TEXT_CHARS:
            return None  # scanned award, no text layer
        excerpt = delivery_excerpt(text)
        if not excerpt:
            return None
        # Agile terms rarely appear in Sections B and F, so their surrounding
        # passages are appended separately. Without them the classifier cannot
        # tell a delivery commitment from a staff qualification.
        agile_context = agile_context_windows(text)
        if agile_context:
            excerpt = f"{excerpt}\n\n=== passages mentioning agile terms ===\n{agile_context}"
        return {
            "contract_number": contract_number,
            "excerpt": excerpt,
            "regex_agile_terms": ",".join(agile_terms_used(text)),
            "regex_strong_terms": ",".join(agile_terms_used(text, strong_only=True)),
            # Two ways the document itself can be wrong, carried through to the
            # published CSV so a reader can drop those rows. They are recorded
            # rather than filtered here: filtering would change the published
            # counts that analysis/verify_claims.py pins, and re-judging labels
            # is a deliberate act, not a side effect of adding a check.
            "excerpt_readable_share": round(readable_share(excerpt), 3),
            "names_other_contract": ",".join(other_contracts_named(text, contract_number)),
        }

    with DCProcurementClient() as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = [r for r in pool.map(read, targets) if r]
    print(f"{len(rows):,} yielded usable contract sections", flush=True)

    unreadable = [r for r in rows if looks_unreadable(r["excerpt"])]
    if unreadable:
        print(f"{len(unreadable):,} of those are mojibake, not text -- flagged, not dropped:",
              flush=True)
        for row in sorted(unreadable, key=lambda r: r["excerpt_readable_share"]):
            print(f"  {row['contract_number']}  printable share "
                  f"{row['excerpt_readable_share']}", flush=True)

    misfiled = [r for r in rows if r["names_other_contract"]]
    if misfiled:
        print(f"{len(misfiled):,} award documents name a contract other than their own "
              "-- flagged, not dropped:", flush=True)
        for row in sorted(misfiled, key=lambda r: r["contract_number"]):
            print(f"  {row['contract_number']}  document names "
                  f"{row['names_other_contract']}", flush=True)

    frame = pd.DataFrame(rows)
    return frame.merge(
        labels[["contract_number", "title", "vendor", "work_type", "ceiling"]],
        on="contract_number",
    )


def apply_overrides(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the hand-read corrections in delivery_overrides.yaml.

    The classifier undercounts genuine agile commitments; the overrides file
    records the cases where hand-reading found one it missed, with the reason.
    Each must also appear in tests/delivery_gold_labels.csv.
    """
    import yaml

    overrides = yaml.safe_load((ROOT / "delivery_overrides.yaml").read_text(encoding="utf-8"))
    for contract_number, spec in (overrides.get("agile_mention_kind") or {}).items():
        mask = frame.contract_number == contract_number
        if mask.any():
            frame.loc[mask, "agile_mention_kind"] = spec["value"]
            print(f"override {contract_number}: agile_mention_kind -> {spec['value']}")
    return frame


def main(model: str = DEFAULT_MODEL) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    candidates = labels[labels["work_type"].isin(BUILD_WORK_TYPES)]
    print(f"{len(candidates):,} contracts where delivery structure is a meaningful question")

    frame = gather_excerpts(candidates)
    print(f"{len(frame):,} have a readable award PDF with usable sections")
    if frame.empty:
        return 1

    records = frame[["title", "vendor", "excerpt"]].to_dict("records")
    predictions = classify_contracts(records, model=model, config_path=DELIVERY_CONFIG)

    for field in ("delivery_structure", "agile_mention_kind", "uses_agile_vocabulary",
                  "agile_quote", "scope_fixed_in_advance", "deliverables_quote",
                  "confidence"):
        frame[field] = [p.get(field) if p else None for p in predictions]

    # How much of the vote backed each label. Below 1.0 means the model read the
    # same excerpt two ways, so the contract is a coin flip rather than a
    # finding, and the counts built from these columns carry model noise --
    # roughly +/-4 at 95% on a count near 80. See dcprocure.classify.
    for field in ("delivery_structure", "agile_mention_kind"):
        frame[f"{field}_agreement"] = [
            (p.get("field_agreement") or {}).get(field) if p else None
            for p in predictions
        ]
    split = (frame["delivery_structure_agreement"] < 1).sum()
    print(f"{split:,} contracts where the repeat draws disagreed on delivery_structure")

    frame = apply_overrides(frame)
    frame.drop(columns=["excerpt"]).to_csv(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH} ({len(frame):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
