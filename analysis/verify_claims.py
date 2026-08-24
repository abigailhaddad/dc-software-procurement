#!/usr/bin/env python3
"""Recompute every number used in the briefing, from the raw snapshot.

Each claim prints with the figure it asserts and the figure the data gives, so a
mismatch is visible rather than something a reader has to go hunting for. Run
this before publishing anything.

    uv run python analysis/verify_claims.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

FAILURES: list[str] = []


def check(claim: str, computed: object, asserted: object, tolerance: float = 0.0) -> None:
    """Compare an asserted figure against what the data actually gives."""
    if isinstance(computed, (int, float)) and isinstance(asserted, (int, float)):
        ok = abs(float(computed) - float(asserted)) <= tolerance
    else:
        ok = computed == asserted
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {claim}\n         asserted={asserted}  computed={computed}")
    if not ok:
        FAILURES.append(claim)


def main() -> int:
    contracts = parse.load_contracts()
    orders = parse.load_purchase_orders()
    payments = parse.load_payments()
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    delivery = pd.read_csv(ROOT / "data" / "delivery_structure.csv")

    print("\n-- corpus --")
    check("contract option periods", len(contracts), 7113)
    check("distinct contracts", contracts.contract_number.nunique(), 3983)
    check("contracts classified", len(labels), 3983)
    check("purchase orders (IT classes)", len(orders), 114255)
    check("payments", len(payments), 866823)
    check("earliest PO fiscal year", int(orders.fiscal_year.min()), 2013)

    print("\n-- what DC buys --")
    tech = labels[labels.is_technology == True]  # noqa: E712
    check("technology contracts", len(tech), 1372)
    check("technology ceilings $M", round(tech.ceiling.sum() / 1e6), 4641, 1)
    for work_type, contracts_n, ceiling_m in [
        ("ordering_vehicle", 328, 1840),
        ("staff_augmentation", 211, 1203),
        ("custom_software_build", 60, 38),
    ]:
        rows = labels[labels.work_type == work_type]
        check(f"{work_type} contracts", len(rows), contracts_n)
        check(f"{work_type} ceilings $M", round(rows.ceiling.sum() / 1e6), ceiling_m, 1)

    print("\n-- ceilings are not spend --")
    # Computed over every contract classified as technology, which is a stable
    # and documented basis. An earlier version used an internal commodity
    # grouping instead and produced 23% and 95%; both were wrong.
    paid = payments.groupby("contract_number")["amount"].sum()
    tech = tech.copy()
    tech["paid"] = tech.contract_number.map(paid).fillna(0.0)
    types = contracts.groupby("contract_number").contract_type.first()
    tech["contract_type"] = tech.contract_number.map(types)
    for label, ctype, pct in [
        ("IDIQ drawdown %", "Indefinite Delivery Indefinite Quantity", 4),
        ("firm fixed price drawdown %", "Firm Fixed Price", 44),
    ]:
        rows = tech[tech.contract_type == ctype]
        check(label, round(100 * rows.paid.sum() / rows.ceiling.sum()), pct, 1)
    check("technology contracts with any linked payment %",
          round(100 * (tech.paid > 0).mean()), 44, 1)

    print("\n-- the payments trap --")
    check("line-level total $B", round(payments.amount.sum() / 1e9, 1), 33.3, 0.1)
    check("check-total field $B", round(payments.check_total.sum() / 1e9, 1), 113.7, 0.1)
    star = payments[payments.contract_number == "CW95481"]
    check("CW95481 correct $M", round(star.amount.sum() / 1e6), 132, 1)
    check("CW95481 if summed wrong $B", round(star.check_total.sum() / 1e9, 1), 17.3, 0.2)
    # The portal is live: counts move a little between pulls, so these carry a
    # tolerance rather than pretending to be exact.
    check("payments with no contract number",
          int(payments.contract_number.isna().sum()), 478700, 200)

    print("\n-- agile language --")
    check("readable award documents", len(delivery), 288)
    mentions = delivery[delivery.agile_mention_kind != "no_agile_language"]
    check("documents mentioning agile", len(mentions), 38)
    commits = delivery[delivery.agile_mention_kind == "delivery_commitment"]
    check("genuine delivery commitments", len(commits), 9)
    check("mentions that are staff qualifications",
          int((delivery.agile_mention_kind == "personnel_qualification").sum()), 14)
    gated = (commits.delivery_structure == "sequential_gated_deliverables").sum()
    check("commitments that are structurally gated", int(gated), 7)
    other = delivery[delivery.agile_mention_kind != "delivery_commitment"]
    check("baseline gated %",
          round(100 * (other.delivery_structure == "sequential_gated_deliverables").mean()), 27, 1)

    print("\n-- vendor concentration (payments to technology contracts, FY2021-2025) --")
    window = payments[
        payments.fiscal_year.between(2021, 2025)
        & payments.contract_number.isin(set(tech.contract_number))
    ].copy()
    window["vendor_key"] = window.vendor.map(parse.normalize_vendor)
    total = window.amount.sum()
    ranked = window.groupby("vendor_key").amount.sum().sort_values(ascending=False)
    check("technology payments FY21-25 $M", round(total / 1e6), 579, 3)
    check("distinct vendors after normalising", ranked.size, 295, 4)
    check("top 1 vendor share %", round(100 * ranked.head(1).sum() / total, 1), 17.4, 0.4)
    check("top 5 vendor share %", round(100 * ranked.head(5).sum() / total, 1), 42.3, 0.6)
    check("top 10 vendor share %", round(100 * ranked.head(10).sum() / total, 1), 52.5, 0.6)
    check("largest vendor is Computer Aid", ranked.index[0], "COMPUTER AID")
    check("largest vendor paid $M", round(ranked.iloc[0] / 1e6, 1), 100.6, 0.5)

    # The alias file is the record of which names were treated as one company.
    # If it stops matching what the rules actually merge, the published
    # concentration figures rest on claims nobody has reviewed.
    import yaml

    aliases = yaml.safe_load((ROOT / "vendor_aliases.yaml").read_text(encoding="utf-8"))
    recorded = {k: sorted(v) for k, v in (aliases.get("merged_by_rule") or {}).items()}
    actual: dict[str, list[str]] = {}
    for raw in window.vendor.dropna().unique():
        actual.setdefault(parse.normalize_vendor(raw), []).append(raw)
    actual = {k: sorted(v) for k, v in actual.items() if len(v) > 1}
    check("alias file matches what the rules merge", actual == recorded, True)
    if actual != recorded:
        missing = sorted(set(actual) - set(recorded))
        stale = sorted(set(recorded) - set(actual))
        print(f"         unrecorded: {missing[:5]}  stale: {stale[:5]}")
        print("         run: uv run python analysis/refresh_vendor_aliases.py")

    print("\n-- the DC Supply Schedule --")
    dcss = tech[tech.title.str.contains("DCSS", case=False, na=False)]
    check("technology contracts naming DCSS", len(dcss), 397, 3)
    check("their ceilings $M", round(dcss.ceiling.sum() / 1e6), 2410, 5)
    check("share of technology ceilings %",
          round(100 * dcss.ceiling.sum() / tech.ceiling.sum()), 52, 1)
    vehicles = tech[tech.work_type == "ordering_vehicle"]
    check("ordering vehicles that are DCSS",
          int(vehicles.title.str.contains("DCSS", case=False, na=False).sum()), 305, 3)

    print("\n-- named systems --")
    named = tech[tech.system_name.notna() & (tech.system_name.astype(str).str.strip() != "")]
    named = named[named.system_name.astype(str).str.lower() != "nan"]
    by_system = named.groupby(named.system_name.astype(str).str.upper().str.strip()).agg(
        contracts=("contract_number", "size"), ceiling=("ceiling", "sum")
    ).sort_values("ceiling", ascending=False)
    check("largest named system is DCAS", by_system.index[0], "DCAS")
    check("DCAS contracts", int(by_system.iloc[0].contracts), 28, 1)
    check("DCAS ceilings $M", round(by_system.iloc[0].ceiling / 1e6, 1), 198.3, 1.0)

    print("\n-- delivery structure --")
    check("sequential and gated contracts", int((delivery.delivery_structure == "sequential_gated_deliverables").sum()), 82)
    waterfall = delivery[delivery.delivery_structure == "sequential_gated_deliverables"]
    check("of those, scope fixed in advance", int((waterfall.scope_fixed_in_advance == "yes").sum()), 74)
    check("of those, also commit to agile",
          int((waterfall.agile_mention_kind == "delivery_commitment").sum()), 7)
    check("agile only as a hiring requirement",
          int((delivery.agile_mention_kind == "personnel_qualification").sum()), 14)

    print("\n-- purchase-order vendor figures --")
    recent = orders[orders.fiscal_year.between(2022, 2026)].copy()
    recent["vendor_key"] = recent.vendor.str.upper().str.strip()
    cai = recent[recent.vendor_key == "COMPUTER AID  INC"]
    check("Computer Aid FY22-26 $M", round(cai.amount.sum() / 1e6), 465, 3)
    check("Computer Aid purchase orders", len(cai), 5787, 40)
    check("Computer Aid median PO $", round(cai.amount.median()), 59944, 200)
    check("Computer Aid distinct agencies", cai.agency.nunique(), 33, 1)
    fast = recent[recent.vendor_key == "FAST ENTERPRISES LLC"]
    check("FAST Enterprises $M", round(fast.amount.sum() / 1e6), 51, 2)
    check("FAST Enterprises purchase orders", len(fast), 11, 1)
    check("FAST median PO $M", round(fast.amount.median() / 1e6, 1), 3.5, 0.2)

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else f'{len(FAILURES)} FAILED: ' + '; '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
