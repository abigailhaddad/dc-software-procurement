"""Summary tables over the pulled DC procurement data.

Each function returns a data frame and does one thing, so results can be checked
against the portal by hand. The measurement choices that matter:

* **Purchase orders, not contracts, for annual spend.** A contract row is an
  option period whose amount is an authorization ceiling; it is not money spent,
  and an IDIQ ceiling may never be drawn down at all. Purchase orders carry a
  real fiscal year and represent money actually committed.
* **Contracts, not purchase orders, for structure.** Only contracts carry
  contract type, market type, and the attached PDFs.
* **NIGP class 920 is the software-services measure.** Classes 918 and 958
  (consulting, management services) are excluded from technology totals: they
  are dominated by Medicaid managed care contracts worth billions, and folding
  them in overstates DC's technology spend by an order of magnitude.
"""

from __future__ import annotations

import pandas as pd

from . import parse as parse_module

from .commodities import IT_ADJACENT, IT_CORE


def spend_by_year(purchase_orders: pd.DataFrame) -> pd.DataFrame:
    """Purchase-order obligations by fiscal year and commodity grouping.

    ``it_adjacent`` is reported as its own column rather than dropped, so the
    size of what is being excluded stays visible.
    """
    table = purchase_orders.pivot_table(
        index="fiscal_year", columns="group", values="amount", aggfunc="sum"
    ).fillna(0.0)
    core = [c for c in table.columns if c != "it_adjacent"]
    table["it_core_total"] = table[core].sum(axis=1)
    return table.sort_index()


def top_vendors(
    purchase_orders: pd.DataFrame,
    nigp_class: str = "920",
    since_fy: int = 2022,
    limit: int = 20,
) -> pd.DataFrame:
    """Vendors ranked by obligations in one commodity class.

    Vendor names are not normalized upstream, so the same company can appear
    under several spellings ("CDW GOVERNMENT LLC" vs "CDW Government"). Names
    are upper-cased and stripped here, which merges the common cases but is not
    a substitute for a real entity resolution pass.
    """
    subset = purchase_orders[
        (purchase_orders["nigp_class"] == nigp_class)
        & (purchase_orders["fiscal_year"] >= since_fy)
    ].copy()
    subset["vendor_key"] = subset["vendor"].str.upper().str.strip()
    grouped = (
        subset.groupby("vendor_key")
        .agg(purchase_orders=("po_number", "size"), obligations=("amount", "sum"))
        .sort_values("obligations", ascending=False)
    )
    grouped["share_pct"] = (100 * grouped["obligations"] / grouped["obligations"].sum()).round(1)
    return grouped.head(limit)


def excluded_adjacent_note(purchase_orders: pd.DataFrame) -> str:
    """One line quantifying what the IT_ADJACENT exclusion leaves out."""
    adjacent = purchase_orders[purchase_orders["nigp_class"].isin(IT_ADJACENT)]
    core = purchase_orders[purchase_orders["nigp_class"].isin(IT_CORE)]
    return (
        f"Excluded NIGP {sorted(IT_ADJACENT)} (consulting/management services): "
        f"${adjacent['amount'].sum() / 1e9:.1f}B across {len(adjacent):,} POs, "
        f"vs ${core['amount'].sum() / 1e9:.1f}B in IT classes proper. "
        "The excluded classes are dominated by Medicaid managed care."
    )


def identification_bounds(purchase_orders: pd.DataFrame, since_fy: int = 2022) -> pd.DataFrame:
    """Bound DC technology spend, because commodity codes cannot pin it down.

    Neither direction of the obvious heuristic works, and both failures are
    worth seeing side by side:

    * Include every class 918/958 purchase order from a vendor that also sells
      in a core IT class, and construction firms come along with it.
    * Require IT to be a meaningful share of the vendor's business, and Computer
      Aid, Inc. -- the District's largest IT staffing vendor at roughly $93M a
      year -- drops out entirely, because none of its purchase orders are coded
      to an IT class at all.

    So this returns a floor and a ceiling rather than a single number. Anything
    tighter needs contract-title review, not commodity codes.
    """
    recent = purchase_orders[purchase_orders["fiscal_year"] >= since_fy].copy()
    recent["vendor_key"] = recent["vendor"].str.upper().str.strip()
    core_groups = ["software_services", "it_goods", "telecom"]

    core = recent[recent["group"].isin(core_groups)]["amount"].sum()
    adjacent = recent[recent["group"] == "it_adjacent"]
    it_vendors = set(recent[recent["group"].isin(core_groups)]["vendor_key"])
    adjacent_it = adjacent[adjacent["vendor_key"].isin(it_vendors)]["amount"].sum()

    years = recent["fiscal_year"].nunique()
    rows = {
        "floor: core IT classes only": core,
        "ceiling: plus all 918/958 from IT-selling vendors": core + adjacent_it,
    }
    table = pd.DataFrame({"total": list(rows.values())}, index=list(rows))
    table["per_year"] = table["total"] / years
    return table


def spend_by_work_type(
    classifications: pd.DataFrame,
    payments: pd.DataFrame,
    since_fy: int | None = None,
) -> pd.DataFrame:
    """Money actually paid, grouped by what the contract was buying.

    This is the join the commodity codes could not support: classifications key
    on contract number, and payments carry a contract number, so recorded
    disbursements can be attributed to deliverable work versus hourly labor.

    Two caveats travel with every figure here. Payments made under a task order
    are booked against the task order's own contract number, so an
    ``ordering_vehicle`` row understates the work ordered through it. And a
    payment whose contract number is not in the classified set is excluded
    entirely rather than guessed at -- the ``coverage`` figure in
    :func:`classification_coverage` says how much that is.
    """
    paid = payments if since_fy is None else payments[payments["fiscal_year"] >= since_fy]
    totals = paid.groupby("contract_number")["amount"].sum()

    labelled = classifications.copy()
    labelled["paid"] = labelled["contract_number"].map(totals).fillna(0.0)

    table = labelled.groupby("work_type").agg(
        contracts=("contract_number", "size"),
        ceiling=("ceiling", "sum"),
        paid=("paid", "sum"),
    )
    table["paid_share_pct"] = (100 * table["paid"] / table["paid"].sum()).round(1)
    return table.sort_values("paid", ascending=False)


def classification_coverage(
    classifications: pd.DataFrame,
    payments: pd.DataFrame,
) -> pd.DataFrame:
    """How much of the payment record the classified contracts account for.

    Reported so that any share-of-spend claim can be read against the base it is
    computed on, rather than presented as if it covered all District spending.
    """
    known = set(classifications["contract_number"])
    matched = payments["contract_number"].isin(known)
    return pd.DataFrame(
        {
            "payments": [int(matched.sum()), int((~matched).sum()), len(payments)],
            "dollars": [
                payments.loc[matched, "amount"].sum(),
                payments.loc[~matched, "amount"].sum(),
                payments["amount"].sum(),
            ],
        },
        index=["matched to a classified contract", "unmatched", "all payments"],
    )


def vendor_concentration(
    classifications: pd.DataFrame,
    payments: pd.DataFrame,
    since_fy: int = 2021,
    until_fy: int = 2025,
    limit: int = 20,
) -> pd.DataFrame:
    """Vendors ranked by payments against technology contracts.

    Vendor names are normalised first. The District records the same company
    under several spellings -- CDW Government appears three ways in this window
    alone -- and leaving them apart understates concentration. Which spellings
    were treated as one company is recorded in ``vendor_aliases.yaml``.

    Covers only payments that carry a contract number matching a classified
    contract, which is a minority of all District payments. Read the shares as
    shares of traceable technology spending.
    """
    technology = set(
        classifications[classifications["is_technology"] == True]["contract_number"]  # noqa: E712
    )
    window = payments[
        payments["fiscal_year"].between(since_fy, until_fy)
        & payments["contract_number"].isin(technology)
    ].copy()
    window["vendor_key"] = window["vendor"].map(parse_module.normalize_vendor)

    ranked = window.groupby("vendor_key")["amount"].sum().sort_values(ascending=False)
    table = ranked.to_frame("paid")
    table["share_pct"] = (100 * table["paid"] / table["paid"].sum()).round(1)
    table["cumulative_pct"] = table["share_pct"].cumsum().round(1)
    table["paid_M"] = (table["paid"] / 1e6).round(1)
    table["spellings"] = [
        len(window[window.vendor_key == k]["vendor"].dropna().unique()) for k in table.index
    ]
    return table[["paid_M", "share_pct", "cumulative_pct", "spellings"]].head(limit)
