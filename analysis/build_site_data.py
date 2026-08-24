#!/usr/bin/env python3
"""Build site/data.js, the drill-down data behind the briefing page.

The page's tables carry the same figures verify_claims.py checks. This script
supplies what sits underneath them: the contracts behind each category, vendor
and system, so a reader can click a number and see what it is made of.

    uv run python analysis/build_site_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

OUT = ROOT / "site" / "data.js"


def records(frame: pd.DataFrame, cols: list[str], n: int | None = None) -> list[dict]:
    out = frame[cols] if n is None else frame[cols].head(n)
    return json.loads(out.to_json(orient="records"))


def main() -> int:
    contracts = parse.load_contracts()
    payments = parse.load_payments()
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    delivery = pd.read_csv(ROOT / "data" / "delivery_structure.csv")
    manifest = json.load(open(ROOT / "data" / "raw" / "manifest.json"))

    tech = labels[labels.is_technology == True].copy()  # noqa: E712
    tech["vkey"] = tech.vendor.map(parse.normalize_vendor)
    ccols = ["contract_number", "title", "vendor", "agency", "ceiling", "work_type"]

    # contracts behind each work-type row
    work_types = {}
    for work_type, rows in tech.groupby("work_type"):
        rows = rows.sort_values("ceiling", ascending=False)
        work_types[work_type] = {
            "n": len(rows),
            "ceiling": float(rows.ceiling.sum()),
            "contracts": records(rows, ccols, 15),
        }

    # the FY2021-2025 window behind the authorised-against-paid table
    cwin = contracts[contracts.contract_number.isin(set(tech.contract_number))].copy()
    cwin["start"] = pd.to_datetime(cwin.start_date, errors="coerce")
    cwin["end"] = pd.to_datetime(cwin.end_date, errors="coerce")
    lo, hi = pd.Timestamp("2020-10-01"), pd.Timestamp("2025-09-30")
    inwin = cwin[(cwin.start >= lo) & (cwin.end <= hi)]
    win_paid = payments[payments.fiscal_year.between(2021, 2025)].groupby("contract_number")["amount"].sum()
    g = inwin.groupby("contract_number").agg(
        title=("title", "first"), ceiling=("amount", "sum"), ctype=("contract_type", "first")
    )
    g["paid"] = g.index.map(win_paid).fillna(0.0)
    g["vendor"] = g.index.map(tech.set_index("contract_number").vendor.to_dict())
    g = g.reset_index()
    window = {}
    for ctype in ["Indefinite Delivery Indefinite Quantity", "Firm Fixed Price", "Labor Hour"]:
        rows = g[g.ctype == ctype].sort_values("ceiling", ascending=False)
        window[ctype] = {
            "n": len(rows),
            "ceiling": float(rows.ceiling.sum()),
            "paid": float(rows.paid.sum()),
            "contracts": records(rows, ["contract_number", "title", "vendor", "ceiling", "paid"], 10),
        }

    # the ten vendors behind the concentration figures
    win = payments[
        payments.fiscal_year.between(2021, 2025)
        & payments.contract_number.isin(set(tech.contract_number))
    ].copy()
    win["vkey"] = win.vendor.map(parse.normalize_vendor)
    total = float(win.amount.sum())
    ranked = win.groupby("vkey").amount.sum().sort_values(ascending=False)
    vendors = {}
    for vkey, paid in ranked.head(10).items():
        mine = tech[tech.vkey == vkey].sort_values("ceiling", ascending=False)
        vendors[vkey] = {
            "paid": float(paid),
            "share_pct": round(100 * paid / total, 1),
            "raw_names": sorted(win[win.vkey == vkey].vendor.dropna().unique()),
            "contracts": records(mine, ccols, 8),
        }

    # the eleven agile delivery commitments, quotes included
    commits = delivery[delivery.agile_mention_kind == "delivery_commitment"].sort_values(
        "ceiling", ascending=False
    )

    # per delivery structure: the contract language that put contracts in the
    # bucket, so a reader can judge the classification against the words
    structures = {}
    for structure, rows in delivery.groupby("delivery_structure"):
        rows = rows.sort_values("ceiling", ascending=False)
        with_quote = rows[rows.deliverables_quote.notna() & (rows.deliverables_quote.astype(str).str.strip() != "")]
        examples = with_quote.head(8) if len(with_quote) >= 8 else rows.head(8)
        structures[structure] = {
            "n": len(rows),
            "examples": records(
                examples,
                ["contract_number", "title", "vendor", "ceiling",
                 "deliverables_quote", "scope_fixed_in_advance"],
            ),
        }

    # DCAS: every contract naming it
    named = tech[tech.system_name.notna() & (tech.system_name.astype(str).str.strip() != "")]
    named = named[named.system_name.astype(str).str.lower() != "nan"]
    dcas = named[named.system_name.astype(str).str.upper().str.strip() == "DCAS"].sort_values(
        "ceiling", ascending=False
    )

    # the contract with the most option periods, for the method notes
    per = contracts.groupby("contract_number").size()
    star = contracts[contracts.contract_number == per.idxmax()].sort_values("start_date").copy()
    for col in ("start_date", "end_date"):
        star[col] = pd.to_datetime(star[col]).dt.strftime("%Y-%m-%d")

    # published hourly rate cards, from analysis/extract_rates.py
    rates = pd.read_csv(ROOT / "data" / "labor_rates.csv")
    rates["ok"] = rates.pairing.isin(["deterministic", "proximate"])
    rate_contracts = []
    for cn, rows in rates.groupby("contract_number"):
        ok_share = rows.ok.mean()
        entry = {
            "contract_number": cn,
            "vendor": rows.vendor.iloc[0],
            "title": rows.title.iloc[0],
            "work_type": rows.work_type.iloc[0],
            "n": len(rows),
            "min": float(rows.hourly_rate.min()),
            "median": float(rows.hourly_rate.median()),
            "max": float(rows.hourly_rate.max()),
            "pairing_ok_share": round(float(ok_share), 2),
        }
        # per-category rows are only shown where the category-rate pairing
        # verified; otherwise the card appears as a distribution only
        if ok_share >= 0.8:
            shown = rows[rows.ok].sort_values(["labor_category", "hourly_rate"])
            entry["rows"] = records(shown, ["labor_category", "level", "hourly_rate"])
        rate_contracts.append(entry)
    rate_contracts.sort(key=lambda e: -e["n"])

    dev_pat = (
        r"(?:develop|programmer|software|application|computer scientist"
        r"|devops|full[ -]?stack|web design|sdet)"
    )
    dev = rates[rates.ok & rates.labor_category.str.contains(dev_pat, case=False, regex=True)]
    # option-year tables repeat identical rates; one row per distinct price
    dev = dev.drop_duplicates(
        subset=["contract_number", "labor_category", "level", "hourly_rate"]
    ).sort_values("hourly_rate", ascending=False)

    cw95481 = payments[payments.contract_number == "CW95481"]
    data = {
        "pulled_on": manifest["pulled_on"],
        "work_types": work_types,
        "window": window,
        "window_totals": {
            "n": len(g), "ceiling": float(g.ceiling.sum()), "paid": float(g.paid.sum()),
        },
        "vendors": vendors,
        "concentration": {
            "total": total,
            "n_vendors": int(ranked.size),
            "top10": [
                {"vendor": k, "paid": float(v), "share_pct": round(100 * v / total, 1)}
                for k, v in ranked.head(10).items()
            ],
        },
        "rates": {
            "totals": {
                "contracts": int(rates.contract_number.nunique()),
                "rates": int(len(rates)),
                "verified_pairing": int(rates.ok.sum()),
                "min": float(rates.hourly_rate.min()),
                "median": float(rates.hourly_rate.median()),
                "max": float(rates.hourly_rate.max()),
            },
            "contracts": rate_contracts,
            "software": records(
                dev,
                ["contract_number", "vendor", "labor_category", "level", "hourly_rate"],
            ),
        },
        "structures": structures,
        "commitments": records(
            commits,
            ["contract_number", "title", "vendor", "ceiling", "delivery_structure", "agile_quote"],
        ),
        "dcas": records(dcas, ccols),
        "option_example": {
            "contract_number": per.idxmax(),
            "title": star.title.iloc[0],
            "rows": records(star, ["option_period", "amount", "start_date", "end_date"]),
        },
        "paytrap": {
            "rows": int(len(cw95481)),
            "correct": float(cw95481.amount.sum()),
            "wrong": float(cw95481.check_total.sum()),
            "table_correct_B": round(float(payments.amount.sum()) / 1e9, 1),
            "table_wrong_B": round(float(payments.check_total.sum()) / 1e9, 1),
        },
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        "// Generated by analysis/build_site_data.py. Do not edit by hand.\n"
        f"const DATA = {json.dumps(data, indent=1)};\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
