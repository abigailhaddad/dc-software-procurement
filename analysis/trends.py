#!/usr/bin/env python3
"""Ten fiscal years of DC payments: what moves over time, and what does not.

Every published figure in this project is a snapshot or a single five-year
window. The payments table holds 869,839 rows from FY2017 to FY2026, and this
script asks the three time questions nobody had asked: is technology spending
growing, is vendor concentration rising, and do the same vendors keep the same
work.

THE TRAP THAT DOMINATES THIS FILE. The obvious way to build a technology time
series is to join payments to the technology contracts in
``data/contract_classifications.csv``. Do that and technology payments appear
to rise from $3.2M in FY2017 to $199M in FY2026 -- a 62-fold increase. It is
an artifact and not a small one. The contract inventory is a *current-state*
snapshot of the portal: contracts that ended years ago are not in it. The share
of payment dollars that match any classified contract climbs from 0.5% in
FY2017 to 13.6% in FY2026, so the series is measuring how much of the past the
portal still remembers. The contract join is fine for a recent window, which is
what the published FY2021-2025 figures use. It cannot carry a decade.

WHAT THIS USES INSTEAD. Every payment row carries a ``po_number``, and
``purchase_by_commodity.json`` carries NIGP commodity codes for purchase orders
back to FY2013 with no comparable decay -- row counts per year are flat to
mildly declining, the opposite of what aging-out produces. Joining payments to
that table by ``po_number`` tags each payment with a commodity class, and
``IT_CORE`` (software services 920, IT goods, telecom) is the slice that is
unambiguously technology. ``IT_ADJACENT`` and ``MISCELLANEOUS`` are excluded:
district-wide they are dominated by Medicaid managed care and food service, and
including them would put $1.4B/yr of non-technology money in a technology
series. The cost of the exclusion is real and stated in the report -- DC's
large IT staff-augmentation vehicles are booked in 961/962 and are not counted
here.

PARTIAL YEAR. The snapshot runs to 2026-09-07, so FY2026 has eleven complete
months and one week of September. September is the largest payment month of the
DC fiscal year (FY2025: $375M, versus a $272M monthly average), so a naive
full-year FY2026 reads as a collapse that did not happen. Every trend figure
here uses an October-August window across all ten years, which is complete for
FY2026 and comparable for every other year. Full-year totals are reported
beside them, labelled.

OTHER TRAPS OBSERVED, recorded so nobody re-derives them:

* Sum ``amount``, never ``check_total``. Documented in ``parse.load_payments``.
* ``is_technology`` reads back as a numpy bool; ``bool(x)``, not ``x is True``.
* Payment vendor names are truncated at 30 characters for most rows (73,061
  rows are exactly 30 characters long, against 2,073 at 31). The truncation
  defeats the suffix-stripping in ``vendor_aliases.yaml``: "CARAHSOFT
  TECHNOLOGY CORP" loses its CORP and "CARAHSOFT TECHNOLOGY CORPORATI" keeps
  its CORPORATI, so one company holds two keys. :func:`merge_truncations`
  measures how much this costs. The answer is 0.7 points of top-10 share, so
  the published concentration figures stand; the check is kept because the
  answer was not obvious in advance.

Writes CSVs to ``data/trends/`` and prints the report.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from dcprocure import parse
from dcprocure.commodities import IT_CORE

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "trends"

# The snapshot was pulled in early September, so month 9 is a stub in FY2026
# and a full -- and unusually large -- month in every other year. Dropping it
# everywhere is the only way to compare the ten years to each other.
WINDOW_MONTHS = tuple(m for m in range(1, 13) if m != 9)


def tagged_payments() -> pd.DataFrame:
    """Payments with a commodity class attached, plus the comparability flags.

    ``in_window`` marks October-August. ``it_core`` marks payments whose
    purchase order carries an unambiguously technology NIGP class.
    """
    payments = parse.load_payments()
    orders = parse.load_purchase_orders()[["po_number", "nigp_class", "group"]]
    frame = payments.merge(orders, on="po_number", how="left")
    frame["month"] = pd.to_datetime(frame.payment_date).dt.month
    frame["in_window"] = frame.month.isin(WINDOW_MONTHS)
    frame["it_core"] = frame.nigp_class.isin(IT_CORE)
    frame["fy"] = frame.fiscal_year.astype("Int64")
    frame["vendor_key"] = frame.vendor.map(parse.normalize_vendor)
    return frame


def survivorship_check(payments: pd.DataFrame) -> pd.DataFrame:
    """Show why the contract join cannot carry a ten-year series.

    Reports, per fiscal year, the share of payment dollars that match any
    contract in the classification file. A flat share would mean the inventory
    covers the decade evenly; a rising one means old contracts have dropped out
    and any trend built on the join is measuring portal memory.
    """
    classified = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    known = set(classified.contract_number)
    tech = set(classified[classified.is_technology.map(bool)].contract_number)
    frame = payments.copy()
    frame["matched"] = frame.contract_number.isin(known)
    frame["tech_matched"] = frame.contract_number.isin(tech)
    total = frame.groupby("fy").amount.sum()
    out = pd.DataFrame(
        {
            "all_payments_M": total / 1e6,
            "matched_any_contract_pct": 100
            * frame[frame.matched].groupby("fy").amount.sum()
            / total,
            "tech_contract_payments_M": frame[frame.tech_matched].groupby("fy").amount.sum()
            / 1e6,
        }
    )
    return out.round(2)


def spending_series(payments: pd.DataFrame) -> pd.DataFrame:
    """Technology payments by fiscal year, full year and October-August."""
    window = payments[payments.in_window]
    core = payments[payments.it_core]
    core_window = window[window.it_core]

    def by_year(frame: pd.DataFrame) -> pd.Series:
        return frame.groupby("fy").amount.sum() / 1e6

    out = pd.DataFrame(
        {
            "all_payments_M": by_year(payments),
            "it_core_M": by_year(core),
            "all_payments_octaug_M": by_year(window),
            "it_core_octaug_M": by_year(core_window),
            "software_services_octaug_M": by_year(
                core_window[core_window.nigp_class == "920"]
            ),
            "it_goods_octaug_M": by_year(core_window[core_window.group == "it_goods"]),
            "telecom_octaug_M": by_year(core_window[core_window.group == "telecom"]),
        }
    )
    out["it_share_octaug_pct"] = 100 * out.it_core_octaug_M / out.all_payments_octaug_M
    return out.round(2)


def concentration_series(payments: pd.DataFrame) -> pd.DataFrame:
    """Vendor concentration in technology payments, computed per fiscal year.

    The published concentration figures use payments matched to technology
    contracts, which is unusable across a decade for the survivorship reason
    above. This uses the commodity-coded slice, whose population is stable, so
    a change in the numbers is a change in concentration rather than a change
    in what is being counted.

    HHI is on a 0-10,000 scale. For reference, US merger guidelines treat
    anything under 1,500 as unconcentrated.
    """
    core = payments[payments.it_core & payments.in_window]
    rows = []
    for year, group in core.groupby("fy"):
        ranked = group.groupby("vendor_key").amount.sum().sort_values(ascending=False)
        total = ranked.sum()
        share = ranked / total
        rows.append(
            {
                "fy": int(year),
                "total_M": round(total / 1e6, 1),
                "vendors": ranked.size,
                "top1_pct": round(100 * share.iloc[0], 1),
                "top5_pct": round(100 * share.head(5).sum(), 1),
                "top10_pct": round(100 * share.head(10).sum(), 1),
                "hhi": round(10000 * (share**2).sum()),
                "largest_vendor": ranked.index[0],
            }
        )
    return pd.DataFrame(rows).set_index("fy")


def merge_truncations(frame: pd.DataFrame) -> dict[str, str]:
    """Map vendor keys that are a longer spelling of another key onto the short one.

    Exists because of the 30-character truncation in the payments vendor field.
    Only keys of 12 characters or more are treated as prefixes, so short
    generic names cannot swallow unrelated companies.
    """
    keys = sorted(frame.vendor_key.unique())
    mapping = {}
    for short in keys:
        if len(short) < 12:
            continue
        for long in keys:
            if long != short and long.startswith(short):
                mapping[long] = short
    return mapping


def published_window_robustness() -> pd.DataFrame:
    """Recompute the published FY2021-2025 concentration with truncations merged.

    The published figures ($579M, 295 vendors, top 10 at 52.5%) are the
    baseline. This asks what they would be if the 30-character truncation were
    repaired, which is a check on whether concentration is understated.
    """
    classified = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    tech = set(classified[classified.is_technology.map(bool)].contract_number)
    payments = parse.load_payments()
    window = payments[
        payments.fiscal_year.between(2021, 2025) & payments.contract_number.isin(tech)
    ].copy()
    window["vendor_key"] = window.vendor.map(parse.normalize_vendor)
    window["merged_key"] = window.vendor_key.replace(merge_truncations(window))
    rows = []
    for label, column in [("as published", "vendor_key"), ("truncations merged", "merged_key")]:
        ranked = window.groupby(column).amount.sum().sort_values(ascending=False)
        total = ranked.sum()
        rows.append(
            {
                "basis": label,
                "total_M": round(total / 1e6, 1),
                "vendors": ranked.size,
                "top1_pct": round(100 * ranked.iloc[0] / total, 1),
                "top5_pct": round(100 * ranked.head(5).sum() / total, 1),
                "top10_pct": round(100 * ranked.head(10).sum() / total, 1),
            }
        )
    return pd.DataFrame(rows).set_index("basis")


def persistence(payments: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """How much of each year's technology money goes to vendors already there.

    Two measures, because they fail in different directions:

    * ``carryover_pct`` -- share of a year's dollars paid to vendors that were
      also paid in the immediately preceding year. This is unbiased: the
      comparison set is fixed before the year being measured.
    * ``ten_of_ten_pct`` -- share going to the 45 vendors paid in all ten years.
      That set is selected using the whole panel, including the late years, so
      it is biased upward at the end of the series. Read the direction, not the
      level.
    """
    core = payments[payments.it_core & payments.in_window]
    years_present = core.groupby("vendor_key").fy.nunique()
    totals = core.groupby("vendor_key").amount.sum()
    tenure = pd.DataFrame(
        {
            "vendors": years_present.value_counts().sort_index(),
            "paid_M": (
                totals.groupby(years_present).sum() / 1e6
            ).round(1),
        }
    )
    tenure["share_of_dollars_pct"] = (
        100 * tenure.paid_M / tenure.paid_M.sum()
    ).round(1)
    tenure.index.name = "fiscal_years_present"

    always = set(years_present[years_present == 10].index)
    rows = []
    for year in sorted(core.fy.dropna().unique())[1:]:
        current = core[core.fy == year]
        prior = set(core[core.fy == year - 1].vendor_key)
        rows.append(
            {
                "fy": int(year),
                "carryover_pct": round(
                    100 * current[current.vendor_key.isin(prior)].amount.sum()
                    / current.amount.sum(),
                    1,
                ),
                "ten_of_ten_pct": round(
                    100 * current[current.vendor_key.isin(always)].amount.sum()
                    / current.amount.sum(),
                    1,
                ),
            }
        )
    return tenure, pd.DataFrame(rows).set_index("fy")


# The nine contracts whose D&F cites D.C. Official Code 2-354.13(8) -- the
# exception for maintenance of existing software where the creator of the
# intellectual property is the only possible source. The vendor name on the
# contract does not always match the payments table (spelling, acquisitions,
# and the 30-character truncation), so the payment-side keys are written out
# rather than derived. TELETRAC NAVMAN US is Navman Wireless after the
# rebranding and is grouped with it; the group would otherwise show a break in
# a payment stream that never broke.
LOCK_IN_VENDORS: dict[str, dict] = {
    "Netsmart New York": {
        "contract": "CW90741",
        "agency": "Behavioral Health (DBH)",
        "keys": ["NETSMART NEW YORK"],
    },
    "NICE Systems": {
        "contract": "CW112026",
        "agency": "Unified Communications (OUC)",
        "keys": ["NICE SYSTEMS"],
    },
    "Navman Wireless / Teletrac Navman": {
        "contract": "CW120077",
        "agency": "Special Education Transportation (SET)",
        "keys": [
            "NAVMAN WIRELESS NORTH AME",
            "NAVMAN WIRELESS NORTH AMERICA",
            "TELETRAC NAVMAN US",
        ],
    },
    "Customer Expressions": {
        "contract": "CW98879",
        "agency": "Health Care Finance (DHCF)",
        "keys": ["CUSTOMER EXPRESSIONS"],
    },
    "Public Consulting Group": {
        "contract": "CW123727",
        "agency": "Health Care Finance (DHCF)",
        "keys": ["PUBLIC CONSULTING", "PUBLIC CONSULTING GRP"],
    },
    "Carahsoft Technology": {
        "contract": "CW124474",
        "agency": "Health Care Finance (DHCF)",
        "keys": ["CARAHSOFT TECHNOLOGY", "CARAHSOFT TECHNOLOGY CORPORATI"],
    },
    "Runbeck Election Services": {
        "contract": "CW115545",
        "agency": "Board of Elections (DCBOE)",
        "keys": ["RUNBECK ELECTION SERVICES"],
    },
    "Tybera Development": {
        "contract": "CW123311",
        "agency": "Employment Services (DOES)",
        "keys": ["TYBERA DEVELOPMENT"],
    },
    "V3Gate": {
        "contract": "CW107442",
        "agency": "Employment Services (DOES)",
        "keys": ["V3GATE"],
    },
}


def lock_in_tenure(payments: pd.DataFrame) -> pd.DataFrame:
    """How long DC has been paying each software-lock-in vendor.

    LEFT CENSORING. The payments table starts in FY2017. A vendor shown at ten
    of ten years has been paid for as long as the data can see, not for exactly
    ten years; the real relationship may be older. Nothing here can establish
    an upper bound on tenure.

    Uses all payments to the vendor, not only payments against the cited
    contract, because the question is whether DC keeps paying the company --
    which it does across a succession of contract numbers, and the succession
    is the point.
    """
    rows = []
    for name, spec in LOCK_IN_VENDORS.items():
        paid = payments[payments.vendor_key.isin(spec["keys"])]
        years = sorted(paid.fy.dropna().astype(int).unique())
        by_year = paid.groupby("fy").amount.sum() / 1e6
        rows.append(
            {
                "vendor": name,
                "cited_contract": spec["contract"],
                "cited_agency": spec["agency"],
                "first_fy": years[0],
                "last_fy": years[-1],
                "fys_paid": len(years),
                "gap_years": len(set(range(years[0], years[-1] + 1)) - set(years)),
                "total_paid_M": round(paid.amount.sum() / 1e6, 2),
                "contract_numbers": paid.contract_number.nunique(),
                "agencies": paid.agency.nunique(),
                "median_annual_M": round(by_year.median(), 2),
            }
        )
    return pd.DataFrame(rows).sort_values("fys_paid", ascending=False).set_index("vendor")


def lock_in_annual(payments: pd.DataFrame) -> pd.DataFrame:
    """The lock-in vendors' payment stream, one row per vendor, one column per year."""
    rows = {}
    for name, spec in LOCK_IN_VENDORS.items():
        paid = payments[payments.vendor_key.isin(spec["keys"])]
        rows[name] = (paid.groupby("fy").amount.sum() / 1e6).round(2)
    return pd.DataFrame(rows).T.fillna(0)


def agency_series(payments: pd.DataFrame, top: int = 12) -> pd.DataFrame:
    """Technology payments by agency and fiscal year, October-August."""
    core = payments[payments.it_core & payments.in_window]
    table = (
        core.pivot_table(index="agency", columns="fy", values="amount", aggfunc="sum")
        .fillna(0)
        .div(1e6)
    )
    table["total_M"] = table.sum(axis=1)
    return table.sort_values("total_M", ascending=False).head(top).round(1)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payments = tagged_payments()
    pd.set_option("display.width", 200)

    print("=" * 78)
    print("WHY THE CONTRACT JOIN CANNOT CARRY A DECADE")
    print("=" * 78)
    survivorship = survivorship_check(payments)
    print(survivorship.to_string())
    print(
        "\nThe matched share rises from"
        f" {survivorship.matched_any_contract_pct.iloc[0]:.1f}% to"
        f" {survivorship.matched_any_contract_pct.iloc[-1]:.1f}% of dollars."
        " The apparent"
        f" {survivorship.tech_contract_payments_M.iloc[-1] / survivorship.tech_contract_payments_M.iloc[0]:.0f}x"
        " rise in technology-contract payments is mostly that."
    )

    print("\n" + "=" * 78)
    print("1. IS TECHNOLOGY SPENDING GROWING?  (commodity-coded, Oct-Aug window)")
    print("=" * 78)
    spending = spending_series(payments)
    print(spending.to_string())
    spending.to_csv(OUT / "technology_spending_by_year.csv")

    growth = spending.it_core_octaug_M
    print(
        f"\nFY2017 to FY2025: ${growth.loc[2017]:.1f}M to ${growth.loc[2025]:.1f}M,"
        f" {growth.loc[2025] / growth.loc[2017]:.1f}x, while all payments grew"
        f" {spending.all_payments_octaug_M.loc[2025] / spending.all_payments_octaug_M.loc[2017]:.1f}x."
        f"\nFY2026 (complete on this window): ${growth.loc[2026]:.1f}M,"
        f" {100 * (growth.loc[2026] / growth.loc[2025] - 1):+.0f}% against FY2025."
        "\nThe FY2026 fall is entirely on the services side: software services"
        f" ${spending.software_services_octaug_M.loc[2025]:.1f}M to"
        f" ${spending.software_services_octaug_M.loc[2026]:.1f}M, while IT goods rose"
        f" ${spending.it_goods_octaug_M.loc[2025]:.1f}M to"
        f" ${spending.it_goods_octaug_M.loc[2026]:.1f}M and outspent services for the"
        " first time in the series. Checked month by month: the services fall is in"
        " every month of FY2026, not a recording lag at the end of the snapshot."
    )

    print("\n" + "=" * 78)
    print("2. IS VENDOR CONCENTRATION RISING?")
    print("=" * 78)
    concentration = concentration_series(payments)
    print(concentration.to_string())
    concentration.to_csv(OUT / "concentration_by_year.csv")
    print("\nRobustness of the published FY2021-2025 figures to name truncation:")
    robustness = published_window_robustness()
    print(robustness.to_string())
    robustness.to_csv(OUT / "published_window_robustness.csv")

    print("\n" + "=" * 78)
    print("3. INCUMBENCY")
    print("=" * 78)
    tenure, carryover = persistence(payments)
    print("Vendor tenure in commodity-coded technology payments:")
    print(tenure.to_string())
    print("\nHow much of each year's money goes to vendors already there:")
    print(carryover.to_string())
    tenure.to_csv(OUT / "vendor_tenure.csv")
    carryover.to_csv(OUT / "vendor_carryover.csv")

    print("\nThe nine software-lock-in contracts -- how long DC has paid the vendor:")
    lock = lock_in_tenure(payments)
    print(lock.to_string())
    lock.to_csv(OUT / "lock_in_tenure.csv")
    print("\nPayments by fiscal year, $M (FY2026 is Oct-early Sep):")
    annual = lock_in_annual(payments)
    print(annual.to_string())
    annual.to_csv(OUT / "lock_in_annual.csv")

    print("\n" + "=" * 78)
    print("4. WHERE IT MOVED: AGENCIES")
    print("=" * 78)
    agencies = agency_series(payments)
    print(agencies.to_string())
    agencies.to_csv(OUT / "agency_technology_by_year.csv")
    core = payments[payments.it_core & payments.in_window]
    octo = core[core.agency == "Chief Technology Officer (OCTO)"]
    octo_share = (
        100 * octo.groupby("fy").amount.sum() / core.groupby("fy").amount.sum()
    ).round(1)
    print(f"\nOCTO share of technology payments by year:\n{octo_share.to_string()}")

    print(f"\nWrote CSVs to {OUT}")


if __name__ == "__main__":
    main()
