"""Normalize raw portal JSON into tidy data frames.

The portal returns money as strings like ``"$1,234.56"`` and dates as
``"8/21/2026"``. This module converts those, derives DC fiscal years, and adds
the classification columns the analysis leans on.

Two shape facts drive everything downstream and are worth stating plainly:

1. A contract search row is **one option period**, not one contract. A five-year
   contract appears as up to six rows, each with its own amount and dates.
   ``contract_number`` is what identifies the contract; ``id`` identifies the
   period. Summing ``amount`` over rows gives a lifetime ceiling, never an
   annual figure.
2. ``contract_type`` mixes two different things -- how the work is priced
   (fixed price vs. labor hour) and what kind of vehicle it is (IDIQ,
   Requirements). For an IDIQ the pricing is set on each task order and is not
   visible in this field at all. :func:`pricing_model` keeps those apart rather
   than guessing.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .commodities import IT_ALL, class_of, group_for
from .pull import RAW_DIR

def parse_money(value: Any) -> float:
    """Parse ``"$1,234.56"`` to a float. Blank or unparseable becomes NaN."""
    if value is None:
        return float("nan")
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text or text in {"-", "N/A"}:
        return float("nan")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        amount = float(text)
    except ValueError:
        return float("nan")
    return -amount if negative else amount


def parse_date(value: Any) -> date | None:
    """Parse the portal's ``M/D/YYYY`` dates.

    The archive contains ``12/30/1899`` as a null-date sentinel; it is returned
    as ``None`` so it cannot be mistaken for a real event.
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value).strip(), "%m/%d/%Y").date()
    except ValueError:
        return None
    return None if parsed.year < 1950 else parsed


def fiscal_year(day: date | None) -> float:
    """DC fiscal year for a date. FY2026 runs 2025-10-01 to 2026-09-30."""
    if day is None:
        return float("nan")
    return day.year + 1 if day.month >= 10 else day.year


def _load(name: str, raw_dir: Path) -> list[dict[str, Any]]:
    return json.loads((raw_dir / name).read_text(encoding="utf-8"))


def load_contracts(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Contract option periods joined to their detail records.

    Returns one row per option period, with ``contract_type`` and
    ``pricing_model`` merged in from the detail pull.
    """
    rows = _load("contracts_by_commodity.json", raw_dir)
    details = {d["id"]: d for d in _load("contract_details.json", raw_dir)}

    records = []
    for row in rows:
        detail = details.get(row["id"], {})
        classes = sorted({class_of(c) for c in row.get("commodityCodes") or []})
        start = parse_date(row.get("startDate"))
        records.append(
            {
                "id": row["id"],
                "contract_number": row["contractNumber"],
                "option_period": row.get("currentOptionPeriod"),
                "title": row.get("title"),
                "vendor": row.get("vendor"),
                "agency": "; ".join(a for a in (row.get("agencyNames") or []) if a),
                "amount": parse_money(row.get("contractAmount")),
                "start_date": start,
                "end_date": parse_date(row.get("endDate")),
                "award_date": parse_date(row.get("awardDate")),
                "start_fy": fiscal_year(start),
                "award_fy": fiscal_year(parse_date(row.get("awardDate"))),
                "nigp_classes": classes,
                "primary_group": _primary_group(classes),
                "contract_type": detail.get("contractType"),
                "market_type": detail.get("marketType"),
                "contracting_officer": detail.get("contractingOfficerName"),
                "vendor_state": detail.get("vendorState"),
                "n_documents": len(detail.get("documents") or []),
                "has_detail": row["id"] in details,
            }
        )
    return pd.DataFrame.from_records(records)


def _primary_group(classes: list[str]) -> str:
    """Pick the most specific IT grouping among a row's NIGP classes.

    A contract tagged both 920 and 918 is a software-services contract that also
    touches consulting, so the more specific label wins.
    """
    priority = ["software_services", "it_goods", "telecom", "it_adjacent", "other"]
    groups = {group_for(c) for c in classes}
    for group in priority:
        if group in groups:
            return group
    return "other"


def load_purchase_orders(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Purchase orders in the IT commodity classes.

    Purchase orders carry the portal's own ``fiscalYear`` field, which -- unlike
    the contract search's fiscal-year filter -- is real. This is the cleanest
    per-year view of what the District actually committed to spend.
    """
    rows = _load("purchase_by_commodity.json", raw_dir)
    records = []
    for row in rows:
        code = str(row.get("commodityCode") or "")
        ordered = parse_date(row.get("orderedDate"))
        records.append(
            {
                "po_number": row.get("poNumber"),
                "agency": row.get("agencyName"),
                "vendor": row.get("supplierName"),
                "amount": parse_money(row.get("poTotal")),
                "nigp_code": code,
                "nigp_class": class_of(code),
                "group": group_for(class_of(code)),
                "fiscal_year": _fy_number(row.get("fiscalYear")),
                "ordered_date": ordered,
            }
        )
    frame = pd.DataFrame.from_records(records)
    return frame[frame["nigp_class"].isin(IT_ALL)].copy()


def _fy_number(value: Any) -> float:
    """Turn ``"FY2026"`` into ``2026.0``."""
    text = str(value or "").strip().upper().removeprefix("FY")
    try:
        return float(text)
    except ValueError:
        return float("nan")


def commodity_labels(raw_dir: Path = RAW_DIR) -> dict[str, str]:
    """Map three-digit NIGP class to its official description."""
    return {c["code"]: c["description"] for c in _load("commodities.json", raw_dir)}


def load_payments(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Vendor payments -- money that actually left the District.

    This is the whole payments universe (~867k rows, FY2017 onward), because the
    module's filters are ignored server-side and it returns everything anyway.

    **Sum ``amount``, never ``check_total``.** The portal's ``paymentAmount``
    field is not this line's amount: it is the total of the whole check or ACH
    run the voucher was paid in, repeated identically on every row belonging to
    that run. Summing it double-counts enormously -- across the full table it
    totals $113.7B against $33.3B of actual vouchers, and one staffing contract
    with a $120M ceiling appears to have been paid $17.4B. ``voucherAmount`` is
    the line-level figure and is what ``amount`` holds here; ``check_total`` is
    kept under its own name so it cannot be summed by accident.

    ``contract_number`` links a payment to a contract, which makes it possible
    to compare an authorization ceiling against real drawdown. One further
    caveat governs every such comparison: **task orders carry their own contract
    numbers**. A DC Supply Schedule vehicle can therefore show near-zero
    payments against it even when work was ordered underneath, because the money
    is recorded against the child task order instead. Low drawdown on an IDIQ is
    evidence about where payments are booked, not proof the vehicle went unused.
    """
    rows = _load("payments.json", raw_dir)
    records = []
    for row in rows:
        paid = parse_date(row.get("paymentDate"))
        records.append(
            {
                "payment_id": row.get("id"),
                "contract_number": row.get("contractNumber"),
                "po_number": row.get("poNumber"),
                "voucher_number": row.get("voucherNumber"),
                "agency": row.get("agencyName"),
                "vendor": row.get("vendorName"),
                "amount": parse_money(row.get("voucherAmount")),
                "check_total": parse_money(row.get("paymentAmount")),
                "payment_date": paid,
                "fiscal_year": fiscal_year(paid),
            }
        )
    return pd.DataFrame.from_records(records)


# Vendor identity rules live in vendor_aliases.yaml, not here. Deciding that two
# differently spelled names are the same company is a judgment call, and it
# moves published figures: CDW Government is recorded under three spellings in
# the technology payments alone. The file records both the rules and what they
# merge, so the claims can be reviewed without reading code.
ALIASES_PATH = Path(__file__).resolve().parents[2] / "vendor_aliases.yaml"


@lru_cache(maxsize=1)
def _alias_rules(path: Path = ALIASES_PATH) -> tuple[re.Pattern[str] | None, bool]:
    """Load and compile the normalisation rules. Cached; the file is small."""
    if not path.exists():
        return None, True
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    suffixes = config.get("strip_suffixes") or []
    pattern = (
        re.compile(r"\b(" + "|".join(re.escape(s) for s in suffixes) + r")\b")
        if suffixes
        else None
    )
    return pattern, bool(config.get("strip_punctuation", True))


def normalize_vendor(name: Any) -> str:
    """Collapse a vendor name to a comparable key.

    Applies the rules in ``vendor_aliases.yaml``: strip punctuation, drop
    corporate suffixes, squeeze whitespace. That merges spelling variants and
    nothing cleverer -- it will not catch acquisitions, trading names, or a
    genuine misspelling, so treat the result as better than the raw field rather
    than as resolved identity. The file's ``keep_separate`` block records pairs
    deliberately left unmerged.

    >>> normalize_vendor("CDW Government, Inc.")
    'CDW GOVERNMENT'
    """
    if not isinstance(name, str):
        return ""
    suffix_pattern, strip_punctuation = _alias_rules()
    text = name.upper()
    if strip_punctuation:
        text = re.sub(r"[^A-Z0-9 ]", " ", text)
    if suffix_pattern is not None:
        text = suffix_pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()
