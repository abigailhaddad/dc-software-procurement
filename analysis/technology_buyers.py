#!/usr/bin/env python3
"""Who in DC government actually buys technology, and how much can be attributed.

    uv run python analysis/technology_buyers.py

The starting suspicion was that agency attribution is broken: 62% of technology
ceilings are booked either to the central procurement shop (OCP) or to nobody at
all, which looks like a records failure. It mostly is not. Nearly all of that
money is ordering vehicles and supply-schedule pool awards -- standing capacity
that has no customer yet because nothing has been ordered against it. Once those
are set aside the genuinely unattributed residual is 18 contracts and 2.4% of
ceilings, and most of those are OCP buying for its own operations.

Three tables see the buyer differently and the script builds all three:

* **contracts** carry ``agency``, but a fifth of technology contracts leave it
  blank and OCP is recorded on another sixth. Ceilings are authorisations.
* **payments** carry ``agency`` too, and it is the agency whose money moved.
  Never blank, OCP almost absent -- but payments reach only 606 of the 1,383
  technology contracts, because task orders under a vehicle carry their own
  contract numbers and the parent link is not published anywhere in this data.
* **purchase orders** carry ``agency`` and a commodity code and no contract
  number at all. They are the only view that covers agencies whose contracts
  never enter the portal, and DCPS and OCFO are both in that position.

TRAPS this script exists to not fall into:

* ``is_technology`` comes back from pandas as ``numpy.bool_``. ``x is True`` is
  False for it. Cast with ``bool()`` or use the column as a mask, never ``is``.
* A contract number appears once per option period in ``contracts.json``. The
  ``ceiling`` column of contract_classifications.csv is already the sum over
  periods (verified against the raw table here), so this script uses that and
  never re-sums.
* Purchase orders in the IT_ADJACENT and MISCELLANEOUS classes (918, 958, 961,
  962) total $20.4B and are dominated by DGS construction misfiled into those
  catch-alls. Everything PO-based here is restricted to the core IT classes.
* Ceilings and payments are different quantities. Every figure below says which.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse

OCP = "Contracting and Procurement (OCP)"
OCTO = "Chief Technology Officer (OCTO)"

# Title markers for supply-schedule pool awards. These are not classified as
# ordering_vehicle by the title classifier, but they are the same thing: a
# vendor is placed on a standing schedule and agencies order against it later.
# DCSS is the DC Supply Schedule; TSS is Temporary Support Services; MOBIS and
# MMPI are further schedules; "Contract Workspace (Procurement)" is OCP's own
# placeholder shell. None of them has a using agency at award time.
POOL_MARKERS = {
    "DCSS": r"DCSS",
    "TSS": r"\bTSS\d|Temporary Support Services|Temporary Personnel",
    "Contract Workspace": r"Contract Workspace",
    "MOBIS": r"\bMOBIS\b",
    "MMPI": r"\bMMPI",
}

# Renamed rather than reassigned: DCRA split into DLCP and DOB in 2022, so a
# payment by one against a contract naming the other is the same buyer under a
# new name, not an agency drawing on somebody else's contract.
SUCCESSOR_PAIRS = {
    ("Licensing & Consumer Protection (DLCP)", "Department Of Buildings (DOB)"),
    ("Department Of Buildings (DOB)", "Licensing & Consumer Protection (DLCP)"),
}

CORE_IT_GROUPS = ["software_services", "it_goods", "telecom"]


def m(value: float) -> str:
    return f"${value / 1e6:,.1f}M"


def agencies_named(value: object) -> list[str]:
    """Split the ``agency`` field, which holds a ';'-joined list of names."""
    return [a.strip() for a in str(value or "").split(";") if a.strip()]


def technology_contracts() -> pd.DataFrame:
    """One row per technology contract, with the buyer-attribution state.

    ``ceiling`` here is the classifications file's own column. It is checked
    against the sum over option periods rather than trusted, because getting
    that wrong inflates every figure in this script.
    """
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    labels["is_technology"] = labels["is_technology"].astype(bool)
    tech = labels[labels["is_technology"]].copy()

    periods = parse.load_contracts()
    summed = (
        periods[periods.contract_number.isin(set(tech.contract_number))]
        .groupby("contract_number")["amount"]
        .sum()
    )
    check = tech.set_index("contract_number")["ceiling"].round(2)
    mismatch = int((check != summed.reindex(check.index).round(2)).sum())
    if mismatch:
        raise SystemExit(
            f"ceiling column disagrees with the option-period sum on {mismatch} contracts"
        )

    tech["agency"] = tech["agency"].fillna("").str.strip()
    tech["state"] = "named agency"
    tech.loc[tech.agency == OCP, "state"] = "OCP only"
    tech.loc[tech.agency == "", "state"] = "no agency"

    titles = tech["title"].fillna("")
    tech["pool_marker"] = ""
    for name, pattern in POOL_MARKERS.items():
        hit = titles.str.contains(pattern, case=False, regex=True, na=False)
        tech.loc[hit & (tech.pool_marker == ""), "pool_marker"] = name
    tech["is_pool"] = (tech.work_type == "ordering_vehicle") | (tech.pool_marker != "")
    return tech


def contract_view(tech: pd.DataFrame) -> None:
    total = tech.ceiling.sum()
    print("=" * 72)
    print("1. THE CONTRACT VIEW -- ceilings, i.e. authorisation, not spending")
    print("=" * 72)
    print(f"{len(tech)} technology contracts, {m(total)} of ceilings\n")

    block = tech.groupby("state").agg(
        contracts=("ceiling", "size"), ceiling=("ceiling", "sum")
    )
    block["pct"] = (100 * block.ceiling / total).round(1)
    block["ceiling"] = block.ceiling.map(m)
    print(block.sort_values("pct", ascending=False).to_string())

    unattributed = tech[tech.state != "named agency"]
    print(
        f"\nno identifiable using agency: {len(unattributed)} contracts, "
        f"{m(unattributed.ceiling.sum())} "
        f"({100 * unattributed.ceiling.sum() / total:.1f}% of technology ceilings)"
    )

    pooled = unattributed[unattributed.is_pool]
    residual = unattributed[~unattributed.is_pool]
    print(
        f"  of which standing capacity (vehicles and schedule pools): "
        f"{len(pooled)} contracts, {m(pooled.ceiling.sum())} "
        f"({100 * pooled.ceiling.sum() / unattributed.ceiling.sum():.1f}% of the block)"
    )
    print(
        f"  genuine residual: {len(residual)} contracts, {m(residual.ceiling.sum())} "
        f"({100 * residual.ceiling.sum() / total:.2f}% of technology ceilings)"
    )
    print("\n  the residual, in full:")
    show = residual.sort_values("ceiling", ascending=False)[
        ["contract_number", "state", "work_type", "ceiling", "title"]
    ]
    show["ceiling"] = show.ceiling.map(m)
    print(show.to_string(index=False, max_colwidth=58))

    print("\n  top buyers among contracts that do name one:")
    named = tech[tech.state == "named agency"]
    top = named.groupby("agency").agg(contracts=("ceiling", "size"), ceiling=("ceiling", "sum"))
    top["pct_of_named"] = (100 * top.ceiling / named.ceiling.sum()).round(1)
    top = top.sort_values("ceiling", ascending=False).head(8)
    top["ceiling"] = top.ceiling.map(m)
    print(top.to_string())


def payment_view(tech: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    """Payments against technology contracts, and whether they name a new buyer."""
    print("\n" + "=" * 72)
    print("2. THE PAYMENT VIEW -- money that moved, FY2016-2026")
    print("=" * 72)
    tech_numbers = set(tech.contract_number)
    paid = payments[payments.contract_number.isin(tech_numbers)].copy()

    print(
        f"{len(paid):,} payment rows, {m(paid.amount.sum())}, "
        f"on {paid.contract_number.nunique()} of {len(tech)} technology contracts"
    )
    print(f"payment rows with no agency recorded: {int(paid.agency.isna().sum())}")
    print(f"distinct agencies making technology payments: {paid.agency.nunique()}")
    print(f"paid by OCP: {m(paid.loc[paid.agency == OCP, 'amount'].sum())}")

    reached = tech[tech.contract_number.isin(set(paid.contract_number))]
    print(
        f"\ncontracts reached by any payment: {len(reached)} carrying "
        f"{m(reached.ceiling.sum())} of ceilings "
        f"({100 * reached.ceiling.sum() / tech.ceiling.sum():.1f}%)"
    )
    unattributed = tech[tech.state != "named agency"]
    rescued = unattributed[unattributed.contract_number.isin(set(paid.contract_number))]
    rescued_paid = paid[paid.contract_number.isin(set(rescued.contract_number))].amount.sum()
    print(
        f"contracts with no named agency that payments reach: {len(rescued)} of "
        f"{len(unattributed)}, carrying {m(rescued_paid)} of payments"
    )
    print("  -- payments do not recover the unattributed ceilings; they barely touch them")

    lookup = tech.set_index("contract_number")["agency"]
    paid["contract_agency"] = paid.contract_number.map(lookup)
    named = paid[~paid.contract_agency.isin(["", OCP])].copy()
    named["match"] = [
        row.agency in agencies_named(row.contract_agency) for row in named.itertuples()
    ]
    mismatch = named[~named.match].copy()
    mismatch["successor"] = [
        any((a, row.agency) in SUCCESSOR_PAIRS for a in agencies_named(row.contract_agency))
        for row in mismatch.itertuples()
    ]
    real = mismatch[~mismatch.successor]
    print(
        f"\nwhere the contract names a real agency ({m(named.amount.sum())} of payments):"
    )
    print(f"  paid by that agency:                {m(named[named.match].amount.sum())}")
    print(f"  paid by a renamed successor agency: {m(mismatch[mismatch.successor].amount.sum())}")
    print(
        f"  paid by a different agency:         {m(real.amount.sum())} "
        f"({100 * real.amount.sum() / named.amount.sum():.1f}%)"
    )

    per_contract = (
        real.groupby("contract_number")["amount"].sum().sort_values(ascending=False)
    )
    top = per_contract.head(5)
    print(
        f"\n  concentrated on {len(per_contract)} contracts; the top 3 are "
        f"{100 * per_contract.head(3).sum() / per_contract.sum():.1f}% of it:"
    )
    frame = pd.DataFrame(
        {
            "paid": top.map(m),
            "contract_agency": lookup.reindex(top.index),
            "title": tech.set_index("contract_number")["title"].reindex(top.index),
        }
    )
    print(frame.to_string(max_colwidth=48))
    return paid


def shared_vehicles(tech: pd.DataFrame, paid: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("3. CONTRACTS BOUGHT BY MORE THAN ONE AGENCY")
    print("=" * 72)
    per = paid.groupby("contract_number").agg(
        agencies=("agency", "nunique"), amount=("amount", "sum")
    )
    multi = per[per.agencies > 1]
    print(
        f"{len(multi)} of {len(per)} paid technology contracts are paid by more than one "
        f"agency, but they carry {m(multi.amount.sum())} of "
        f"{m(per.amount.sum())} paid ({100 * multi.amount.sum() / per.amount.sum():.1f}%)"
    )
    top = multi.sort_values("amount", ascending=False).head(6)
    frame = pd.DataFrame(
        {
            "agencies": top.agencies,
            "paid": top.amount.map(m),
            "contract_agency": tech.set_index("contract_number")["agency"].reindex(top.index),
            "title": tech.set_index("contract_number")["title"].reindex(top.index),
        }
    )
    print(frame.to_string(max_colwidth=44))

    star = paid[paid.contract_number == "CW95481"]
    if len(star):
        by_agency = star.groupby("agency")["amount"].sum().sort_values(ascending=False)
        ceiling = float(tech.set_index("contract_number").loc["CW95481", "ceiling"])
        print(
            f"\n  OCTO STaR II (CW95481): ceiling {m(ceiling)}, paid {m(by_agency.sum())}, "
            f"drawn on by {len(by_agency)} agencies"
        )
        for agency, amount in by_agency.head(5).items():
            print(f"    {agency:<45} {m(amount):>10}  {100 * amount / by_agency.sum():4.1f}%")
        print(
            f"    OCTO's own share of the vehicle it holds: "
            f"{100 * by_agency.get(OCTO, 0) / by_agency.sum():.1f}%"
        )


def purchase_order_view(
    tech: pd.DataFrame, orders: pd.DataFrame, paid: pd.DataFrame, contracts: pd.DataFrame
) -> None:
    print("\n" + "=" * 72)
    print("4. THE PURCHASE-ORDER VIEW -- core IT commodity classes only")
    print("=" * 72)
    core = orders[orders.group.isin(CORE_IT_GROUPS)]
    window = core[core.fiscal_year.between(2017, 2025)]
    total = window.amount.sum()
    print(
        f"FY2017-2025: {len(window):,} orders, {m(total)}, "
        f"{window.agency.nunique()} agencies, {int(window.agency.isna().sum())} blank"
    )
    by_agency = window.groupby("agency")["amount"].sum().sort_values(ascending=False)
    for k in (1, 3, 5, 10):
        print(f"  top {k:>2} agencies = {100 * by_agency.head(k).sum() / total:.1f}%")
    print(f"  agencies ordering over $1M of core IT: {int((by_agency > 1e6).sum())}")

    print("\nOCTO's share of core IT purchase orders, by fiscal year:")
    per_year = window.groupby("fiscal_year")["amount"].sum()
    octo_year = window[window.agency == OCTO].groupby("fiscal_year")["amount"].sum()
    table = pd.DataFrame(
        {"total": per_year.map(m), "octo": octo_year.map(m),
         "octo_pct": (100 * octo_year / per_year).round(1)}
    )
    print(table.to_string())

    print("\nagencies that order core IT but hold no technology contract in this data:")
    held = {a for value in tech.agency for a in agencies_named(value)}
    # Wider net: every agency named on any contract in the IT-commodity pull,
    # technology-classified or not. An agency absent from this set holds no
    # contract in the portal at all, which is a stronger statement.
    any_contract = {a for value in contracts.agency.fillna("") for a in agencies_named(value)}
    paid_by = paid.groupby("agency")["amount"].sum()
    absent = by_agency[[a for a in by_agency.index if a not in held]]
    rows = pd.DataFrame(
        {
            "core_IT_orders": absent,
            "tech_contract_payments": paid_by.reindex(absent.index).fillna(0),
        }
    )
    rows = rows[rows.core_IT_orders > 5e6].sort_values("core_IT_orders", ascending=False)
    rows["in_contract_table"] = ["yes" if a in any_contract else "NO" for a in rows.index]
    out = rows.copy()
    out["core_IT_orders"] = out.core_IT_orders.map(m)
    out["tech_contract_payments"] = out.tech_contract_payments.map(m)
    print(out.to_string())
    print(
        f"\n  {len(rows)} agencies, {m(rows.core_IT_orders.sum())} of core IT ordering, "
        "invisible to any contract-based total of DC technology procurement"
    )
    never = rows[rows.in_contract_table == "NO"]
    print(
        f"  {len(never)} of them ({m(never.core_IT_orders.sum())}) appear on no contract in the "
        "IT-commodity pull at all, under any spelling -- not merely no technology contract"
    )


def centralisation(tech: pd.DataFrame, paid: pd.DataFrame, orders: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("5. IS TECHNOLOGY PROCUREMENT CENTRALISED AT OCTO?")
    print("=" * 72)
    named = tech[tech.state == "named agency"]
    core = orders[orders.group.isin(CORE_IT_GROUPS)]
    po_window = core[core.fiscal_year.between(2017, 2025)]
    pay_window = paid[paid.fiscal_year.between(2017, 2025)]

    measures = [
        ("all technology ceilings", tech.loc[tech.agency == OCTO, "ceiling"].sum(), tech.ceiling.sum()),
        ("ceilings that name an agency", named.loc[named.agency == OCTO, "ceiling"].sum(), named.ceiling.sum()),
        ("technology payments FY2017-2025",
         pay_window.loc[pay_window.agency == OCTO, "amount"].sum(), pay_window.amount.sum()),
        ("core IT purchase orders FY2017-2025",
         po_window.loc[po_window.agency == OCTO, "amount"].sum(), po_window.amount.sum()),
    ]
    for label, octo, total in measures:
        print(f"  {label:<38} OCTO {100 * octo / total:>5.1f}%  of {m(total)}")
    print(
        "\nTwo measures of money that actually moved, built from different tables, "
        "put OCTO at about a quarter of DC technology buying. It is not centralised."
    )


def main() -> int:
    tech = technology_contracts()
    payments = parse.load_payments()
    orders = parse.load_purchase_orders()

    contract_view(tech)
    paid = payment_view(tech, payments)
    shared_vehicles(tech, paid)
    contracts = parse.load_contracts()
    purchase_order_view(tech, orders, paid, contracts)
    centralisation(tech, paid, orders)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
