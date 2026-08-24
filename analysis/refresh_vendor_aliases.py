#!/usr/bin/env python3
"""Regenerate the record of which vendor names get merged.

`vendor_aliases.yaml` holds two different things. The rules at the top are
decisions a person makes. The `merged_by_rule` block at the bottom is a record
of what those rules actually combine in the published figures, regenerated from
the data so the identity claims can be reviewed rather than taken on trust.

This rewrites only that block, leaving the decisions above it untouched.

    uv run python analysis/refresh_vendor_aliases.py
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

ALIASES = ROOT / "vendor_aliases.yaml"
MARKER = "merged_by_rule:\n"


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def main() -> int:
    payments = parse.load_payments()
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    technology = set(labels[labels.is_technology == True].contract_number)  # noqa: E712

    window = payments[
        payments.fiscal_year.between(2021, 2025)
        & payments.contract_number.isin(technology)
    ]
    spend = window.groupby(window.vendor).amount.sum()

    groups: dict[str, set[str]] = collections.defaultdict(set)
    for name in window.vendor.dropna().unique():
        groups[parse.normalize_vendor(name)].add(name)
    merged = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    ranked = sorted(merged.items(), key=lambda kv: -sum(spend.get(n, 0) for n in kv[1]))

    lines = []
    for key, variants in ranked:
        total = sum(spend.get(n, 0) for n in variants)
        lines.append(f"  {_quote(key)}:   # ${total / 1e6:,.1f}M\n")
        lines.extend(f"    - {_quote(n)}\n" for n in variants)

    text = ALIASES.read_text(encoding="utf-8")
    head = text[: text.index(MARKER) + len(MARKER)]
    ALIASES.write_text(head + "".join(lines), encoding="utf-8")
    print(f"recorded {len(ranked)} merge groups in {ALIASES.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
