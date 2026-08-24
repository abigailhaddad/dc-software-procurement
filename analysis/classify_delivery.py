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

    uv run python analysis/classify_delivery.py
"""

from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
    pick_award_document,
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

    def read(target: tuple[str, str]) -> dict[str, str] | None:
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
        }

    with DCProcurementClient() as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = [r for r in pool.map(read, targets) if r]
    print(f"{len(rows):,} yielded usable contract sections", flush=True)

    frame = pd.DataFrame(rows)
    return frame.merge(
        labels[["contract_number", "title", "vendor", "work_type", "ceiling"]],
        on="contract_number",
    )


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

    frame.drop(columns=["excerpt"]).to_csv(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH} ({len(frame):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
