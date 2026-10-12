#!/usr/bin/env python3
"""Is DC's technology money increasingly flowing through resellers?

THE QUESTION. DC technology solicitations often name a product rather than a
supplier, and the contract usually goes to a reseller rather than to the
company that makes the product. That pattern is ordinary government IT and is
not worth publishing on its own. It becomes a finding only if the intermediary
layer is *growing*. This script asks whether it is.

THE CLAIM THIS EXISTS TO CHECK. A quick pass said technology payments against
``work_type == "hardware_or_license_resale"`` contracts grew from $9.4M in
FY2021 to $48.1M in FY2025, with the share rising 37% to 52%. That is built on
the payments-to-contracts join, which ``analysis/trends.py`` documents as
unusable for a time series: the contract inventory is a current-state snapshot,
ended contracts are gone, and the share of payment dollars matching any
classified contract climbs from 0.5% in FY2017 to 13.6% in FY2026. The level
growth is mostly portal memory. :func:`naive_contract_join` reproduces the
number so it can be seen next to the robust one, and is not the answer.

WHAT THIS USES INSTEAD, and the one place it improves on trends.py. Payments
carry ``po_number`` and purchase orders carry NIGP commodity classes back to
FY2013 with no comparable decay (row counts per year are flat to mildly
declining). ``trends.py`` joins those two tables on ``po_number`` as spelled.
That under-joins: the purchase-order table records revised orders with a
version suffix -- ``PO731165-V2`` -- and 34,708 of its 114,435 rows carry one,
while the payments table never does. Stripping the suffix lifts the share of
payment rows that match an IT-classed purchase order from 18.5% to 38.5%, and
no base number maps to two commodity classes, so the merge stays one-to-one.
Everything here joins on the base number. The two scripts are otherwise
comparable: same ``IT_CORE`` definition, same October-August window.

DEFINING A RESELLER, and what the definition misses. Two routes were tried.

* ``work_type == "hardware_or_license_resale"`` inherits the survivorship
  problem above, and -- more damaging -- it does not mean what the question
  needs. It labels contracts that *buy a product*, whoever sells it, so Dell,
  Microsoft, Xerox and Agilent selling their own goods all land in it. Ranking
  vendors by the share of their technology contracts carrying that label puts
  Microsoft and Xerox at 100%. It cannot separate an intermediary from a maker.
* So resellers are named on the vendor side, in :data:`RESELLERS`, and every
  name carries the DC contract titles that justify it: a company is treated as
  a reseller when its own DC technology contracts sell products made by other
  companies, usually several different makers. The list is a judgment call and
  is kept as data so it can be argued with.

  What that misses: firms with no classified DC technology contract to read
  (NET PCN STRATEGIES, $12.2M of FY2023-2026 IT goods, and COPPER RIVER
  INFORMATION TECH, $2.7M in FY2017-2019, have none) are left out for want of
  evidence, which pushes the reseller share *down*; and firms that both resell
  and build (Limbic Systems resells Broadcom and also writes DOB's software)
  are left out rather than guessed at. :func:`sensitivity` reports what the
  series does under a narrower list of unambiguous national VARs.

THE LIST-CONSTRUCTION TRAP, which is the one that could have manufactured this
whole finding. The reseller list was written by reading the vendors who are
large *now*. If resellers that were large in FY2013 have since disappeared,
leaving them off the list would produce a rising share out of nothing. The
check is :func:`era_leaders`, which ranks vendors by four-year era without
using the list at all. It comes out the other way: the two largest IT vendors
of FY2013-2016 are Dell Marketing selling direct ($64.3M) and Infosys Public
Services ($49.7M), and the only reseller-shaped name missing from the first
draft of the list was Mythics ($6.8M), which is now in it. The early years
were OEM-direct-heavy on their own evidence.

OTHER TRAPS, recorded:

* Sum ``amount``, never ``check_total`` (see ``parse.load_payments``).
* ``is_technology`` reads back as a numpy bool; use ``bool(x)``.
* The payments vendor field is truncated at 30 characters and the purchase
  order supplier field at 40, so the same company holds several keys. The
  reseller series is computed on the purchase-order side of the join, which is
  the less truncated of the two, and :data:`RESELLERS` lists every spelling
  seen in either table.
* NIGP classes are frequently wrong. Fry Plumbing and Heating holds $21.5M of
  IT-classed purchase orders and three family-services collaboratives hold $40M
  more. That noise sits in the denominator. It is not year-specific enough to
  make the trend, but it means the *level* of every share here is soft.
* FY2026 is complete on the October-August window and partial on the full year.

Writes CSVs to ``data/reseller_layer/`` and prints the report.

    uv run python analysis/reseller_layer.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse
from dcprocure.commodities import IT_CORE, IT_GOODS

OUT = ROOT / "data" / "reseller_layer"

#: October-August, the window trends.py uses, for the reason given there: the
#: snapshot stops in early September and September is the largest payment month
#: of the DC fiscal year, so a full-year FY2026 reads as a collapse.
WINDOW_MONTHS = tuple(m for m in range(1, 13) if m != 9)

# The same pinned decade as analysis/trends.py, for the same reason: a pull made
# after 1 October carries a stub of the new fiscal year.
FIRST_FY, LAST_FY = 2017, 2026

#: Companies whose DC technology work is selling other companies' products.
#: The value is the evidence: products named in this vendor's own DC contract
#: titles, or the firm's line of business where it is a national distributor.
#: Spelling variants are separate keys because the vendor fields are truncated.
RESELLERS: dict[str, str] = {
    # National and regional value-added resellers and software distributors.
    "CARAHSOFT TECHNOLOGY": "government software distributor; DC: Salesforce, Accela, ServiceNow",
    "CARAHSOFT TECHNOLOGY CORPORATI": "same company, 30-character truncation",
    "CDW GOVERNMENT": "national IT reseller",
    "CDW": "same company, truncated",
    "SHI INTERNATIONAL": "national software reseller",
    "SOFTWARE HOUSE INTERNATIONAL": "SHI under its full name",
    "DLT SOLUTIONS": "software distributor (Oracle, Autodesk, AWS)",
    "PRESIDIO": "national IT integrator and reseller",
    "IRON BOW TECHNOLOGIES": "national VAR; DC: Cisco Meraki hardware and software",
    "INSIGHT PUBLIC SECTOR": "national IT reseller",
    "IMMIX TECHNOLOGY": "immixGroup, government software distributor",
    "EC AMERICA": "immixGroup subsidiary, government software distributor",
    "MAINLINE INFORMATION SYSTEMS": "IBM VAR; DC: IBM Z13 and Z15 hardware",
    "MYTHICS": "Oracle VAR",
    "VION": "storage VAR; DC: Hitachi Virtual Storage Platform",
    "SOFTWARE INFORMATION RESOURCE": "DC: Box licences, Qlik software",
    "DYNAMIC SYSTEMS": "DC: Oracle licences, support and maintenance",
    "SUN MANAGEMENT": "DC: Palo Alto support, Infoblox appliances",
    "N2GRATE": "Cisco VAR; DC: Cisco firewalls, switches, access points",
    "N2GRATE GOVERNMENT SERVICES": "same company as recorded on purchase orders",
    "DISYS SOLUTIONS": "Cisco VAR; DC: Cisco wifi, video, Ironport",
    "ADVANCED NETWORK CONSULTING": "DC: Dell laptops, FOIAXpress, Cohesity, Data Advantage",
    "NETWORKING FOR FUTURE": "DC: Cisco phones and equipment, Splunk renewal",
    "MVS": "DC: Dell equipment, SolarWinds, KnowBe4, BMC",
    "ABC TECHNICAL SOLUTIONS": "DC: Dell workstations and laptops, IT equipment BPAs",
    "AVID SYSTEMS": "DC: Dell equipment, KnowBe4, Cayosoft",
    # Local firms whose DC technology contracts are third-party products.
    "CRADLE SYSTEMS": "DC: Oracle TOAD, Dynatrace, Tenable",
    "SUPRETECH": "DC: Dell XPS, Dell and Apple computers, QAS licences",
    "CORPORATE SYSTEMS RESOURCES": "DC: DocuSign, Smartsheet, IT equipment BPA",
    "PUBLIC PERFORMANCE MANAGEMENT": "DC: Dell subscriptions, Granicus, IT equipment BPAs",
    "PUBLIC PERFORMANCE MANAG": "same company, 30-character truncation",
    "PITTMAN VANTIX": "DC: SecureAuth, Genetec, Load Runner",
    "BLACKWOOD ASSOCIATES": "DC: Splunk, Gigamon",
    "NJ3Q TECHNOLOGY": "DC: OpenText, SonarQube, Veeam",
    "CHANGING TECHNOLOGIES": "DC: Ivanti, Jira and Confluence",
    "ICI SYSTEMS": "DC: Invicti, NetBrain, Riverbed",
    "CAPITAL CONSULTING THARSEO IT": "DC: Oracle SOA Suite, PeopleSoft, Exadata",
    "HI TECH SOLUTION": "DC: Dell laptops and docking stations",
    "CLOUDAI TECHNOLOGIES": "DC: Dell Mobile Precision workstations",
    "EXECUTIVE INFORMATION SYS": "DC: SAS licences, SAS Viya",
    "DOCUMENT SYSTEMS": "DC: IBM FileNet licences and support",
    "DIGI DOCS DOCUMENT MGERS": "DC: Oracle StorageTek hardware maintenance",
    "VTECH SOLUTION": "DC: McAfee endpoint security renewal",
}

#: The subset that needs no local knowledge: firms whose whole business is
#: reselling other people's technology, recognisable outside DC. Used only for
#: the sensitivity check, where it is the conservative floor.
NATIONAL_VARS = {
    "CARAHSOFT TECHNOLOGY",
    "CARAHSOFT TECHNOLOGY CORPORATI",
    "CDW GOVERNMENT",
    "CDW",
    "SHI INTERNATIONAL",
    "SOFTWARE HOUSE INTERNATIONAL",
    "DLT SOLUTIONS",
    "PRESIDIO",
    "IRON BOW TECHNOLOGIES",
    "INSIGHT PUBLIC SECTOR",
    "IMMIX TECHNOLOGY",
    "EC AMERICA",
    "MAINLINE INFORMATION SYSTEMS",
    "MYTHICS",
    "VION",
}

#: Companies selling their own product. Not exhaustive -- the long tail of
#: single-product software vendors is not worth enumerating -- so the
#: OEM-direct share computed from it is a floor, and is reported as one. It
#: exists to test the mechanism: if the reseller share is rising because DC
#: stopped buying direct, this series has to fall.
OEM_DIRECT = {
    "DELL MARKETING L P",
    "DELL COMPUTER",
    "DELL FINANCIAL SERVICES",
    "APPLE",
    "LENOVO USA",
    "MICROSOFT",
    "ORACLE AMERICA",
    "INTERNATIONAL BUSINESS MACHINE",
    "CISCO SYSTEMS",
    "XEROX",
    "CANON SOLUTIONS AMERICA",
    "CANON FINANCIAL SERVICES",
    "MOTOROLA SOLUTIONS",
    "AVAYA",
    "AVAYA FEDERAL SOLUTIONS",
    "AXON ENTERPRISE",
    "HITACHI VANTARA",
    "SOFTWARE AG USA",
    "ENVIRONMENTAL SYSTEMS RESEARCH",
    "ACCELA",
    "QUICKBASE",
    "POWERSCHOOL",
    "MARK43",
    "FAST ENTERPRISES",
    "SAGITEC SOLUTIONS",
    "GEOGRAPHIC SOLUTIONS",
    "INTRADO LIFE SAFETY",
    "MERATIVE US L P",
    "NETSMART TECHNOLOGIES",
    "INSTRUCTURE",
    "GARTNER",
    "RACKSPACE US",
    "NICE SYSTEMS",
    "SKILLSOFT",
    "AT T",
    "AT T MOBILITY",
}


def base_po(series: pd.Series) -> pd.Series:
    """Strip the ``-V2`` revision suffix so the two tables can be joined.

    Purchase orders are re-filed with a version suffix when revised; payments
    always carry the bare number. No base number maps to two commodity classes,
    so this stays a one-to-one merge.
    """
    return series.astype(str).str.replace(r"-V\d+$", "", regex=True)


def tagged_payments() -> pd.DataFrame:
    """Payments joined to their purchase order's commodity class and supplier.

    The supplier name comes from the purchase-order side because that field is
    truncated at 40 characters against the payments field's 30.
    """
    payments = parse.load_payments()
    orders = parse.load_purchase_orders()
    orders = orders.assign(base=base_po(orders.po_number))
    payments = payments.assign(base=base_po(payments.po_number))
    frame = payments.merge(
        orders[["base", "nigp_class", "group", "vendor"]].rename(
            columns={"vendor": "po_vendor"}
        ),
        on="base",
        how="left",
    )
    frame["month"] = pd.to_datetime(frame.payment_date).dt.month
    frame["in_window"] = frame.month.isin(WINDOW_MONTHS)
    frame["fy"] = frame.fiscal_year.astype("Int64")
    frame = frame[frame.fy.between(FIRST_FY, LAST_FY)].copy()
    frame["vendor_key"] = frame.po_vendor.map(parse.normalize_vendor)
    return frame


def join_coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """The survivorship check this series has to pass, per fiscal year.

    Reports the share of all payment dollars that match an IT-classed purchase
    order. A rising share would mean the same aging-out that ruins the contract
    join. Compare with the contract join's 0.5% to 13.6%.
    """
    total = frame.groupby("fy").amount.sum()
    matched = frame[frame.nigp_class.notna()].groupby("fy").amount.sum()
    out = pd.DataFrame(
        {
            "all_payments_M": total / 1e6,
            "matched_it_po_M": matched / 1e6,
            "matched_pct": 100 * matched / total,
        }
    )
    return out.round(2)


def naive_contract_join() -> pd.DataFrame:
    """Reproduce the contaminated figure so it can be seen, not so it can be used.

    Payments matched to technology contracts, split by whether the contract's
    ``work_type`` is ``hardware_or_license_resale``. This is the series that
    said $9.4M to $48.1M. Its denominator is a portal snapshot whose coverage of
    the past decays, so both the level and the composition move with what the
    portal still remembers.
    """
    classified = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    tech = classified[classified.is_technology.map(bool)]
    resale = set(tech[tech.work_type == "hardware_or_license_resale"].contract_number)
    payments = parse.load_payments()
    payments = payments[payments.contract_number.isin(set(tech.contract_number))]
    payments = payments.assign(fy=payments.fiscal_year.astype("Int64"))
    total = payments.groupby("fy").amount.sum()
    part = payments[payments.contract_number.isin(resale)].groupby("fy").amount.sum()
    out = pd.DataFrame(
        {
            "tech_contract_payments_M": total / 1e6,
            "resale_work_type_M": part / 1e6,
            "resale_share_pct": 100 * part / total,
        }
    )
    return out.loc[2017:2026].round(1)


def share_series(frame: pd.DataFrame, classes: dict[str, str], label: str) -> pd.DataFrame:
    """Reseller and OEM-direct shares of one commodity slice, by fiscal year."""
    slice_ = frame[frame.nigp_class.isin(classes) & frame.in_window]
    total = slice_.groupby("fy").amount.sum()
    reseller = slice_[slice_.vendor_key.isin(RESELLERS)].groupby("fy").amount.sum()
    national = slice_[slice_.vendor_key.isin(NATIONAL_VARS)].groupby("fy").amount.sum()
    oem = slice_[slice_.vendor_key.isin(OEM_DIRECT)].groupby("fy").amount.sum()
    out = pd.DataFrame(
        {
            f"{label}_M": total / 1e6,
            "reseller_M": reseller / 1e6,
            "reseller_pct": 100 * reseller / total,
            "national_var_pct": 100 * national / total,
            "oem_direct_pct_floor": 100 * oem / total,
        }
    )
    return out.round(1)


def commitment_series() -> pd.DataFrame:
    """The same question on purchase orders rather than payments, back to FY2013.

    Purchase orders are commitments, not money out of the door, so this is a
    different measurement of the same thing rather than a confirmation of it.
    What it buys is four more years: the payments table starts in FY2017, and
    the composition shift is already underway by then.
    """
    orders = parse.load_purchase_orders()
    orders = orders.assign(vendor_key=orders.vendor.map(parse.normalize_vendor))
    core = orders[orders.nigp_class.isin(IT_CORE)]
    goods = orders[orders.nigp_class.isin(IT_GOODS)]
    rows = {}
    for name, frame in [("it_core", core), ("it_goods", goods)]:
        total = frame.groupby("fiscal_year").amount.sum()
        reseller = frame[frame.vendor_key.isin(RESELLERS)].groupby("fiscal_year").amount.sum()
        oem = frame[frame.vendor_key.isin(OEM_DIRECT)].groupby("fiscal_year").amount.sum()
        rows[f"{name}_M"] = total / 1e6
        rows[f"{name}_reseller_pct"] = 100 * reseller / total
        rows[f"{name}_oem_pct_floor"] = 100 * oem / total
    out = pd.DataFrame(rows)
    out.index = out.index.astype("Int64")
    return out.round(1)


def era_leaders(top: int = 12) -> pd.DataFrame:
    """Largest IT-goods suppliers by four-year era, computed without the list.

    This is the check on how :data:`RESELLERS` was built. The list was written
    by reading vendors who are large now; if the resellers of FY2013 have since
    vanished, leaving them off would fabricate a rising share. Ranking each era
    on its own terms shows what the early years actually looked like.
    """
    orders = parse.load_purchase_orders()
    orders = orders.assign(vendor_key=orders.vendor.map(parse.normalize_vendor))
    goods = orders[orders.nigp_class.isin(IT_GOODS)]
    frames = []
    for lo, hi in [(2013, 2016), (2017, 2019), (2020, 2022), (2023, 2026)]:
        era = goods[goods.fiscal_year.between(lo, hi)]
        ranked = (era.groupby("vendor_key").amount.sum().sort_values(ascending=False) / 1e6)
        for rank, (vendor, value) in enumerate(ranked.head(top).items(), start=1):
            frames.append(
                {
                    "era": f"FY{lo}-{hi}",
                    "rank": rank,
                    "vendor": vendor,
                    "paid_M": round(value, 1),
                    "on_reseller_list": vendor in RESELLERS,
                    "on_oem_list": vendor in OEM_DIRECT,
                }
            )
    return pd.DataFrame(frames)


def sensitivity(frame: pd.DataFrame) -> pd.DataFrame:
    """Endpoints of the IT-core reseller share under three vendor definitions.

    If the answer depends on which borderline local firms are called resellers,
    it is not an answer.
    """
    slice_ = frame[frame.nigp_class.isin(IT_CORE) & frame.in_window]
    total = slice_.groupby("fy").amount.sum()
    definitions = {
        "national VARs only": NATIONAL_VARS,
        "full list": set(RESELLERS),
        "full list minus the three largest": set(RESELLERS) - {"MVS", "CDW GOVERNMENT", "CDW"},
    }
    rows = []
    for name, vendors in definitions.items():
        share = 100 * slice_[slice_.vendor_key.isin(vendors)].groupby("fy").amount.sum() / total
        rows.append(
            {
                "definition": name,
                "FY2017_pct": round(share.loc[2017], 1),
                "FY2021_pct": round(share.loc[2021], 1),
                "FY2025_pct": round(share.loc[2025], 1),
                "FY2026_pct": round(share.loc[2026], 1),
                "change_2017_2026_pts": round(share.loc[2026] - share.loc[2017], 1),
            }
        )
    return pd.DataFrame(rows).set_index("definition")


def who_is_the_rise(frame: pd.DataFrame) -> pd.DataFrame:
    """Which resellers the rise actually is.

    :func:`sensitivity` shows the whole trend collapsing when MVS and CDW are
    taken out, so this names the money rather than leaving that as a footnote.
    Reports each reseller's share of IT-core payments in the first and last
    three years of the series.
    """
    slice_ = frame[frame.nigp_class.isin(IT_CORE) & frame.in_window]
    early = slice_[slice_.fy.between(2017, 2019)]
    late = slice_[slice_.fy.between(2024, 2026)]
    rows = []
    for key in sorted(set(RESELLERS)):
        paid_early = early[early.vendor_key == key].amount.sum()
        paid_late = late[late.vendor_key == key].amount.sum()
        if paid_early + paid_late < 2e6:
            continue
        rows.append(
            {
                "vendor": key,
                "FY2017_19_M": round(paid_early / 1e6, 1),
                "FY2024_26_M": round(paid_late / 1e6, 1),
                "FY2017_19_pct": round(100 * paid_early / early.amount.sum(), 1),
                "FY2024_26_pct": round(100 * paid_late / late.amount.sum(), 1),
            }
        )
    out = pd.DataFrame(rows).sort_values("FY2024_26_M", ascending=False)
    return out.set_index("vendor")


def without_infosys() -> pd.DataFrame:
    """The commitment series with Infosys Public Services removed.

    Infosys holds $49.4M of FY2013-2016 IT-goods purchase orders -- 25% of the
    era -- for what was systems integration on DC's tax and benefits systems,
    not goods. One miscoded vendor that large sets the base year of the whole
    series, so the series is recomputed without it. If the trend needs Infosys
    to exist, it does not exist.
    """
    orders = parse.load_purchase_orders()
    orders = orders.assign(vendor_key=orders.vendor.map(parse.normalize_vendor))
    orders = orders[orders.vendor_key != "INFOSYS PUBLIC SERVICES"]
    goods = orders[orders.nigp_class.isin(IT_GOODS)]
    total = goods.groupby("fiscal_year").amount.sum()
    reseller = goods[goods.vendor_key.isin(RESELLERS)].groupby("fiscal_year").amount.sum()
    oem = goods[goods.vendor_key.isin(OEM_DIRECT)].groupby("fiscal_year").amount.sum()
    out = pd.DataFrame(
        {
            "it_goods_M": total / 1e6,
            "reseller_pct": 100 * reseller / total,
            "oem_pct_floor": 100 * oem / total,
        }
    )
    out.index = out.index.astype("Int64")
    return out.round(1)


def dell_titles() -> pd.DataFrame:
    """Who holds the contracts whose titles name a Dell product.

    The mechanism behind the share shift, read off the contract titles rather
    than inferred from the money. ``RIDDELL`` is excluded: it is a sporting
    goods company caught by a substring match.
    """
    classified = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    classified = classified.assign(vendor_key=classified.vendor.map(parse.normalize_vendor))
    named = classified[
        classified.title.str.contains("dell", case=False, na=False)
        & ~classified.vendor_key.str.contains("RIDDELL")
    ]
    counts = named.vendor_key.value_counts()
    out = pd.DataFrame({"contracts": counts})
    out["holder"] = [
        "Dell itself" if key.startswith("DELL") else ("reseller" if key in RESELLERS else "other")
        for key in out.index
    ]
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 200)
    payments = tagged_payments()

    print("=" * 78)
    print("0. THE FIGURE THIS REPLACES, AND WHY IT CANNOT STAND")
    print("=" * 78)
    naive = naive_contract_join()
    print(naive.to_string())
    print(
        "\nThat is the payments-to-contracts join. Its denominator decays: the share"
        "\nof all payment dollars matching any classified contract runs 0.5% in FY2017"
        "\nto 13.6% in FY2026 (analysis/trends.py). Level growth on it is portal memory."
    )

    print("\n" + "=" * 78)
    print("1. THE JOIN THIS USES INSTEAD, AND ITS COVERAGE BY YEAR")
    print("=" * 78)
    coverage = join_coverage(payments)
    print(coverage.to_string())
    coverage.to_csv(OUT / "join_coverage.csv")
    print(
        f"\nMatched share runs {coverage.matched_pct.min():.1f}% to"
        f" {coverage.matched_pct.max():.1f}% across ten years, against 0.5% to 13.6%"
        "\nfor the contract join. It drifts up at the end and that drift is the main"
        "\nremaining threat to the last two years of the series."
    )

    print("\n" + "=" * 78)
    print("2. RESELLER SHARE OF TECHNOLOGY PAYMENTS  (IT_CORE, Oct-Aug)")
    print("=" * 78)
    core = share_series(payments, IT_CORE, "it_core")
    print(core.to_string())
    core.to_csv(OUT / "reseller_share_it_core.csv")

    print("\nProduct buying only (IT goods: hardware and preprogrammed software):")
    goods = share_series(payments, IT_GOODS, "it_goods")
    print(goods.to_string())
    goods.to_csv(OUT / "reseller_share_it_goods.csv")

    print("\n" + "=" * 78)
    print("3. THE SAME QUESTION ON COMMITMENTS, BACK TO FY2013")
    print("=" * 78)
    commitments = commitment_series()
    print(commitments.to_string())
    commitments.to_csv(OUT / "reseller_share_commitments.csv")

    print("\n" + "=" * 78)
    print("4. WAS THE VENDOR LIST BUILT OUT OF THE ANSWER?")
    print("=" * 78)
    eras = era_leaders()
    print(eras.to_string(index=False))
    eras.to_csv(OUT / "era_leaders.csv", index=False)

    print("\n" + "=" * 78)
    print("5. SENSITIVITY TO THE DEFINITION")
    print("=" * 78)
    sens = sensitivity(payments)
    print(sens.to_string())
    sens.to_csv(OUT / "sensitivity.csv")

    print(
        "\nThe third row is the one to read. Take MVS and CDW out and the rise is"
        "\n2.6 points over ten years and not monotonic. The trend is those two firms."
    )

    print("\n" + "=" * 78)
    print("6. WHICH RESELLERS THE RISE IS")
    print("=" * 78)
    rise = who_is_the_rise(payments)
    print(rise.to_string())
    rise.to_csv(OUT / "reseller_detail.csv")

    print("\nIT goods commitments with Infosys Public Services removed:")
    clean = without_infosys()
    print(clean.to_string())
    clean.to_csv(OUT / "commitments_without_infosys.csv")

    print("\n" + "=" * 78)
    print("7. THE MECHANISM: WHO SELLS DC ITS DELL EQUIPMENT")
    print("=" * 78)
    dell = dell_titles()
    print(dell.to_string())
    dell.to_csv(OUT / "dell_named_contracts.csv")
    print(
        f"\n{dell.contracts.sum()} contracts name a Dell product in the title;"
        f" {dell[dell.holder == 'Dell itself'].contracts.sum()} are held by a Dell entity."
    )

    print(f"\nWrote CSVs to {OUT}")


if __name__ == "__main__":
    main()
