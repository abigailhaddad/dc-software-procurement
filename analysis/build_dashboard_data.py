#!/usr/bin/env python3
"""Build site/dashboard_data.js, the figures behind the dashboard view.

site/dashboard.html is a second product alongside the briefing: the same data,
read at a glance rather than in order. It carries headline magnitudes, four
charts and a freshness panel, and it links through to index.html for the
narrative and the drill-downs.

Everything it shows is computed here. The page hand-writes no figures, so a
number on the dashboard cannot disagree with the data the way a number typed
into HTML can. Where a figure is also asserted in analysis/verify_claims.py the
two are computed the same way, from the same loaders, on purpose -- the
dashboard must not contradict the briefing.

The drift block is read from data/drift/latest.json, written by
analysis/drift_report.py. That file is optional: a repository that has never
run the drift job has none, and the dashboard says so rather than showing an
empty panel. Its absence is recorded here as drift=None.

That report holds two kinds of finding and the panel shows them separately. A
figure that moved becomes a row in the table, with its before, its after and the
change. A README sentence that no longer matches the data has no such pair -- the
check only asks whether README.md still contains the sentence the current figures
give -- so those are carried as the sentences themselves, in stale_prose.

    uv run python analysis/build_dashboard_data.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

OUT = ROOT / "site" / "dashboard_data.js"
DRIFT = ROOT / "data" / "drift" / "latest.json"

# Display names for the work-type keys, matching the briefing's table.
WORK_TYPE_LABEL = {
    "ordering_vehicle": "Ordering vehicles",
    "staff_augmentation": "Staff augmentation",
    "hardware_or_license_resale": "Hardware and licences",
    "it_consulting_advisory": "Consulting and advisory",
    "cots_implementation": "Product implementation",
    "software_om_support": "Software operations",
    "telecom_or_network": "Telecom and network",
    "custom_software_build": "Custom software development",
}

DELIVERY_LABEL = {
    "recurring_operations": "Recurring operations",
    "sequential_gated_deliverables": "Sequential and gated",
    "goods_or_licenses": "Goods or licences",
    "incremental_capacity": "Incremental capacity",
    "structure_not_stated": "Not stated",
    "milestone_payments_only": "Milestone payments only",
}

# Vendor display names, as the briefing spells them.
VENDOR_LABEL = {
    "COMPUTER AID": "Computer Aid",
    "CDW GOVERNMENT": "CDW Government",
    "KPMG": "KPMG",
    "CARAHSOFT TECHNOLOGY": "Carahsoft Technology",
    "ACCENTURE": "Accenture",
    "GEOGRAPHIC SOLUTIONS": "Geographic Solutions",
    "INTERNATIONAL BUSINESS MACHINE": "IBM",
    "RIDGE IT CYBER": "Ridge IT Cyber",
    "QUICKBASE": "Quickbase",
    "D2SOL": "D2Sol",
}

# What the contracts in each type sell, for the drawdown chart's notes.
CONTRACT_TYPES = [
    ("Indefinite Delivery Indefinite Quantity", "IDIQ"),
    ("Firm Fixed Price", "Firm fixed price"),
    ("Labor Hour", "Labor hour"),
]

RATE_BUCKET_WIDTH = 25
RATE_BUCKET_TOP = 400


def load_drift() -> dict | None:
    """The latest drift report, or None if the weekly job has not run here.

    A missing report is an ordinary state -- the job is scheduled, not
    guaranteed -- and the dashboard renders a panel saying so. An unreadable
    one is not ordinary: a truncated or half-written file would otherwise be
    reported as "no drift report yet" and hide a broken job, so it raises.
    """
    if not DRIFT.exists():
        return None
    report = json.loads(DRIFT.read_text(encoding="utf-8"))
    for key in ("generated_at", "pulled_on", "checked", "moved", "held", "claims"):
        if key not in report:
            raise KeyError(f"{DRIFT} has no {key!r}; drift_report.py schema changed")
    for claim in report["claims"]:
        if "kind" not in claim or "phrase" not in claim:
            raise KeyError(
                f"{DRIFT} claims carry no 'kind'/'phrase'; drift_report.py schema "
                "changed -- rerun: uv run python analysis/drift_report.py"
            )

    # Only the claims that moved are worth carrying; the ones that held are a
    # count. The two kinds go to two places on the page: a figure that moved is
    # a before-and-after row, and a README sentence that no longer matches is
    # just the wording the README needs, with nothing to put in a Change column.
    moved = [c for c in report["claims"] if c["moved"]]
    figures = [c for c in moved if c["kind"] == "figure"]
    # Big movements first, then the rest, so the panel's top rows are the ones a
    # reader needs to see.
    figures.sort(key=lambda c: (c["ok"], -abs(c["change_pct"] or 0.0)))
    stale_prose = [c["phrase"] for c in moved if c["kind"] == "prose"]
    return {
        "generated_at": report["generated_at"],
        "pulled_on": report["pulled_on"],
        "checked": report["checked"],
        "moved": report["moved"],
        "held": report["held"],
        "beyond_tolerance": report.get("beyond_tolerance", 0),
        "error": report["error"],
        "moved_figures": len(figures),
        "moved_claims": [
            {
                "section": c["section"],
                "claim": c["claim"],
                "asserted": c["asserted"],
                "computed": c["computed"],
                "change": c["change"],
                "change_pct": c["change_pct"],
                "ok": c["ok"],
            }
            for c in figures[:40]
        ],
        "moved_claims_shown": min(len(figures), 40),
        "stale_prose": stale_prose,
    }


def rate_histogram(rates: pd.Series) -> list[dict]:
    """The 2,471 published rates in $25 buckets, with a long tail bucket.

    106 rates sit above $400 and one sits at $1,015, so a linear axis run to
    the maximum would be mostly empty. The tail is one bucket, labelled as
    open-ended so it cannot be read as a $25 band like the others.
    """
    buckets = []
    for lo in range(0, RATE_BUCKET_TOP, RATE_BUCKET_WIDTH):
        hi = lo + RATE_BUCKET_WIDTH
        n = int(((rates >= lo) & (rates < hi)).sum())
        buckets.append({"lo": lo, "hi": hi, "label": f"${lo}", "n": n})
    tail = int((rates >= RATE_BUCKET_TOP).sum())
    buckets.append(
        {"lo": RATE_BUCKET_TOP, "hi": None, "label": f"${RATE_BUCKET_TOP}+", "n": tail}
    )
    # Leading empty buckets carry no information; the lowest rate is $24.01.
    while buckets and buckets[0]["n"] == 0:
        buckets.pop(0)
    return buckets


def main() -> int:
    contracts = parse.load_contracts()
    orders = parse.load_purchase_orders()
    payments = parse.load_payments()
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    rates = pd.read_csv(ROOT / "data" / "labor_rates.csv")
    manifest = json.load(open(ROOT / "data" / "raw" / "manifest.json"))

    # The OCR-recovered awards join the natively readable ones, the same way
    # build_site_data.py joins them.
    delivery = pd.concat(
        [
            pd.read_csv(ROOT / "data" / "delivery_structure.csv"),
            pd.read_csv(ROOT / "data" / "delivery_structure_ocr.csv"),
        ],
        ignore_index=True,
    )

    tech = labels[labels.is_technology == True].copy()  # noqa: E712
    tech_numbers = set(tech.contract_number)
    tech_ceiling = float(tech.ceiling.sum())

    # -- headline magnitudes -------------------------------------------------
    work_types = []
    for work_type, rows in tech.groupby("work_type"):
        work_types.append(
            {
                "key": work_type,
                "label": WORK_TYPE_LABEL.get(work_type, work_type),
                "n": int(len(rows)),
                "ceiling": float(rows.ceiling.sum()),
            }
        )
    work_types.sort(key=lambda w: -w["ceiling"])

    # -- authorised against paid, FY2021-FY2025 ------------------------------
    # Option periods falling entirely inside the window, against payments made
    # in the same years. Same construction as verify_claims.py's window check.
    cwin = contracts[contracts.contract_number.isin(tech_numbers)].copy()
    cwin["start"] = pd.to_datetime(cwin.start_date, errors="coerce")
    cwin["end"] = pd.to_datetime(cwin.end_date, errors="coerce")
    inwin = cwin[
        (cwin.start >= pd.Timestamp("2020-10-01")) & (cwin.end <= pd.Timestamp("2025-09-30"))
    ]
    win_paid = (
        payments[payments.fiscal_year.between(2021, 2025)].groupby("contract_number")["amount"].sum()
    )
    grouped = inwin.groupby("contract_number").agg(
        ceiling=("amount", "sum"), ctype=("contract_type", "first")
    )
    grouped["paid"] = grouped.index.map(win_paid).fillna(0.0)

    drawdown = []
    for ctype, label in CONTRACT_TYPES:
        rows = grouped[grouped.ctype == ctype]
        drawdown.append(
            {
                "label": label,
                "n": int(len(rows)),
                "ceiling": float(rows.ceiling.sum()),
                "paid": float(rows.paid.sum()),
            }
        )
    drawdown_total = {
        "label": "All technology contracts",
        "n": int(len(grouped)),
        "ceiling": float(grouped.ceiling.sum()),
        "paid": float(grouped.paid.sum()),
    }

    # -- traceable technology payments by fiscal year ------------------------
    # FY2021-FY2025 only. FY2026 is incomplete in this snapshot (pulled in
    # August, four months before the fiscal year closes) and would read as a
    # collapse rather than a partial year.
    tech_pay = payments[
        payments.contract_number.isin(tech_numbers) & payments.fiscal_year.between(2021, 2025)
    ]
    by_year = tech_pay.groupby("fiscal_year").amount.sum().sort_index()
    payments_by_year = [
        {"fy": int(fy), "amount": float(amount)} for fy, amount in by_year.items()
    ]

    # -- vendor concentration ------------------------------------------------
    win = tech_pay.copy()
    win["vkey"] = win.vendor.map(parse.normalize_vendor)
    traceable_total = float(win.amount.sum())
    ranked = win.groupby("vkey").amount.sum().sort_values(ascending=False)
    top_vendors = [
        {
            "key": vkey,
            "label": VENDOR_LABEL.get(vkey, vkey.title()),
            "paid": float(paid),
            "share_pct": round(100 * paid / traceable_total, 1),
        }
        for vkey, paid in ranked.head(10).items()
    ]

    # -- delivery structure --------------------------------------------------
    structures = []
    for structure, rows in delivery.groupby("delivery_structure"):
        structures.append(
            {
                "key": structure,
                "label": DELIVERY_LABEL.get(structure, structure),
                "n": int(len(rows)),
            }
        )
    structures.sort(key=lambda s: -s["n"])

    # -- published rate cards ------------------------------------------------
    rates_ok = rates.pairing.isin(["deterministic", "proximate"])

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pulled_on": manifest["pulled_on"],
        "corpus": {
            "option_periods": int(len(contracts)),
            "contracts": int(contracts.contract_number.nunique()),
            "purchase_orders": int(len(orders)),
            "payments": int(len(payments)),
            "payments_no_contract_number": int(payments.contract_number.isna().sum()),
            "payments_no_contract_number_amount": float(
                payments[payments.contract_number.isna()].amount.sum()
            ),
        },
        "headline": {
            "tech_contracts": int(len(tech)),
            "tech_ceiling": tech_ceiling,
            "custom_build_n": int((tech.work_type == "custom_software_build").sum()),
            "custom_build_ceiling": float(
                tech[tech.work_type == "custom_software_build"].ceiling.sum()
            ),
            "staff_aug_ceiling": float(
                tech[tech.work_type == "staff_augmentation"].ceiling.sum()
            ),
            "ordering_vehicle_ceiling": float(
                tech[tech.work_type == "ordering_vehicle"].ceiling.sum()
            ),
            "traceable_paid": traceable_total,
            "traceable_vendors": int(ranked.size),
            "rate_count": int(len(rates)),
        },
        "work_types": work_types,
        "drawdown": drawdown,
        "drawdown_total": drawdown_total,
        "payments_by_year": payments_by_year,
        "top_vendors": top_vendors,
        "concentration": {
            "total": traceable_total,
            "n_vendors": int(ranked.size),
            "top1_pct": round(100 * float(ranked.head(1).sum()) / traceable_total, 1),
            "top5_pct": round(100 * float(ranked.head(5).sum()) / traceable_total, 1),
            "top10_pct": round(100 * float(ranked.head(10).sum()) / traceable_total, 1),
        },
        "structures": structures,
        "structures_total": int(len(delivery)),
        "rates": {
            "contracts": int(rates.contract_number.nunique()),
            "count": int(len(rates)),
            "verified_pairing": int(rates_ok.sum()),
            "min": float(rates.hourly_rate.min()),
            "median": float(rates.hourly_rate.median()),
            "max": float(rates.hourly_rate.max()),
            "histogram": rate_histogram(rates.hourly_rate),
        },
        "gaps": {
            # The three things the data cannot show, each with its figure.
            "linked_payment_pct": round(
                100
                * float(
                    tech.contract_number.isin(
                        set(payments[payments.amount > 0].contract_number.dropna())
                    ).mean()
                )
            ),
            "award_docs_readable": int(len(delivery)),
            "award_docs_candidates": int(
                labels.work_type.isin(
                    {
                        "custom_software_build",
                        "cots_implementation",
                        "software_om_support",
                        "it_consulting_advisory",
                    }
                ).sum()
            ),
            "dcss_ceiling_pct": round(
                100
                * float(tech[tech.title.str.contains("DCSS", case=False, na=False)].ceiling.sum())
                / tech_ceiling
            ),
        },
        "drift": load_drift(),
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        "// Generated by analysis/build_dashboard_data.py. Do not edit by hand.\n"
        f"const DASH = {json.dumps(data, indent=1)};\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    if data["drift"] is None:
        print(f"note: {DRIFT.relative_to(ROOT)} not found; the dashboard will say so")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
