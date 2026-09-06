#!/usr/bin/env python3
"""How much ordering activity can be traced to the 330 technology ordering vehicles.

    uv run python analysis/vehicle_orders.py

$1.85B of technology ceiling sits on ordering vehicles that authorise future
orders and buy nothing themselves. The portal has no record type for task
orders and no field linking a child order to its parent vehicle, so the money
ordered underneath has not been followable. This script tries two routes and
reports what each one can and cannot support.

**Route one is measured.** Purchase orders carry no contract number, but the
payments table carries both ``po_number`` and ``contract_number`` on the same
voucher line. Joining purchase orders to payments on the PO number therefore
recovers the contract each order was booked against. This is a real join in the
published data, not an inference: 70,596 of the 114,435 IT-class purchase
orders reach a payment row, and no purchase order in the table is ever booked
against two different contract numbers, so the recovered attribution is
unambiguous where it exists.

Two traps in that join, both handled below:

* The purchase-order table versions its PO numbers (``PO731165-V2``); the
  payments table never does. Matching the raw strings finds 48,463 orders,
  matching on the base number finds 70,596. Stripping the suffix is safe here
  because the base number is unique across the order table -- no two order rows
  collapse onto one base.
* Payments begin in FY2017, so purchase orders before then match almost
  nothing. Match rates run 91-94% from FY2017 on and under 10% before. Every
  figure here is confined to FY2021-2025 for that reason, and because it is the
  window the rest of the project reports on.

**Route two is inferential and turns out not to work.** An order placed against
a schedule goes to a vendor who holds that schedule, so purchase orders to
vehicle-holding vendors bound the activity from above. The bound is real but
loose, and the before-and-after test at the bottom of this script shows why it
cannot be tightened by argument: vendors who won a vehicle were already selling
to the District at almost the same rate beforehand. A vendor-name match picks
up the pre-existing relationship, not the vehicle.

Traps in the vendor route:

* Vendor names in the purchase-order table are truncated at 30 characters
  ("SWIFT ACCOUNTING FINANCIAL" for "SWIFT ACCOUNTING FINANCIAL CONSULTING").
  Normalisation alone leaves 9 vehicle holders unmatched that are plainly the
  same firm, so a prefix rule is applied on top of ``normalize_vendor`` and
  what it merges is printed.
* Option-period end dates do not bound ordering. CW95481 shows a single base
  period ending 2022-12-31 and carries purchase orders through 2026. Only the
  start date is used to bound the vendor route: an order cannot be placed
  against a vehicle that did not exist yet, but it can plainly be placed after
  the portal says the vehicle ended.

Purchase-order amounts are commitments, not payments, and vehicle ceilings are
authorisations. The three are never added together here.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

OUT = ROOT / "data" / "vehicle_orders"

# The window the project reports on, and the window where the purchase-order to
# payment join actually works.
FY_LO, FY_HI = 2021, 2025

# A contract number written into the payments table, wherever it appears in the
# free text. The field is not clean: it holds "GS35F088CA/CW73403",
# "CW106799  (task order CW122648)" and "N/A" alongside bare numbers.
CW_NUMBER = re.compile(r"CW\d{4,7}")

# District contract numbers that are plainly not portal CW numbers. A purchase
# order booked against one of these was placed against something else, which is
# what lets the upper bound come down.
NON_CW_CONTRACT = re.compile(
    r"^(C\d{4,6}|DCKA|DCAM|CFOPD|DCCB|DCTO|GAGA|GS\d|DCHT|DCPO|DCRL)", re.IGNORECASE
)

# Shortest truncated vendor key allowed to stand in for a longer one. Below
# this a prefix match starts joining genuinely different firms.
MIN_PREFIX = 20


def money(value: float) -> str:
    return f"${value / 1e6:,.1f}M"


def vehicle_set(labels: pd.DataFrame) -> pd.DataFrame:
    """The 330 technology contracts classified as ordering vehicles.

    ``is_technology`` reads back from the CSV as a numpy bool, so it is coerced
    rather than compared with ``is True``. One row per contract already --
    contract_classifications.csv is keyed on contract number, not option period.
    """
    tech = labels[labels.is_technology.map(bool)]
    return tech[tech.work_type == "ordering_vehicle"].copy()


def vendor_keys(names: pd.Series, universe: set[str]) -> pd.Series:
    """Normalised vendor keys, with truncated purchase-order spellings recovered.

    ``universe`` is the set of keys seen in the purchase-order table. A contract
    vendor key that no order key matches exactly, but that starts with an order
    key of at least MIN_PREFIX characters, is mapped onto that shorter key --
    the order table cut the name off at 30 characters and the two are the same
    firm. Anything shorter than MIN_PREFIX is left alone.
    """
    keys = names.map(parse.normalize_vendor)
    long_enough = sorted(k for k in universe if len(k) >= MIN_PREFIX)
    recovered: dict[str, str] = {}
    for key in keys.unique():
        if not key or key in universe:
            continue
        for candidate in long_enough:
            if key.startswith(candidate):
                recovered[key] = candidate
                break
    if recovered:
        print(f"  {len(recovered)} vendor names recovered from 30-character truncation:")
        for full, short in sorted(recovered.items()):
            print(f"    {full}  ->  {short}")
    return keys.map(lambda k: recovered.get(k, k))


def attribute_orders(orders: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    """Attach to each purchase order the contract numbers its payments name.

    The base-number rule is checked rather than assumed: if stripping the
    version suffix ever collapsed two order rows onto one base, the mapping
    would be wrong and the assertion fires.
    """
    frame = orders.copy()
    frame["po_base"] = frame.po_number.str.replace(r"-V\d+$", "", regex=True)
    assert frame.po_base.is_unique, "version suffix strip collapsed distinct orders"

    paid = payments[payments.po_number.notna()].copy()
    paid["po_base"] = paid.po_number.astype(str)
    booked = paid.groupby("po_base")["contract_number"].agg(
        lambda s: tuple(sorted(set(s.dropna())))
    )
    frame["booked_to"] = frame.po_base.map(booked)
    frame["in_payments"] = frame.po_base.isin(set(paid.po_base))
    return frame


def classify(booked: object, vehicles: set[str], published: set[str]) -> str:
    """What the payment record says about where a purchase order was placed.

    Returned as a sortable letter prefix: A is traced to a vehicle, B is traced
    to something that is not a vehicle, C and D are degrees of not knowing.
    """
    if not isinstance(booked, tuple):
        return "E_no_payment_record"
    if not booked:
        return "D_payments_name_no_contract"
    named = set(booked)
    embedded = set()
    for text in booked:
        embedded |= set(CW_NUMBER.findall(str(text).upper()))
    if (named | embedded) & vehicles:
        return "A_traced_to_a_vehicle"
    if named & published:
        return "B_traced_to_another_published_contract"
    if any(NON_CW_CONTRACT.match(str(text).strip()) for text in named):
        return "B_traced_to_a_non_CW_contract_number"
    if embedded:
        return "C_traced_to_an_unpublished_CW_number"
    return "D_placeholder_text_only"


def which_vehicle(booked: object, vehicles: set[str]) -> str | None:
    if not isinstance(booked, tuple):
        return None
    found = set(booked)
    for text in booked:
        found |= set(CW_NUMBER.findall(str(text).upper()))
    hits = sorted(found & vehicles)
    return hits[0] if hits else None


def _straddle(
    orders: pd.DataFrame, pairs: list[tuple[str, pd.Timestamp]]
) -> tuple[float, float, int, int, float, int]:
    """Order dollars and counts in the 24 months either side of a date, summed
    over a list of (vendor key, date) pairs."""
    by_vendor = {key: group for key, group in orders.groupby("vendor_key")}
    before = after = cold_start = 0.0
    n_before = n_after = silent = 0
    for key, start in pairs:
        sub = by_vendor.get(key)
        if sub is None:
            silent += 1
            continue
        pre = sub[(sub.ordered_at >= start - pd.DateOffset(years=2)) & (sub.ordered_at < start)]
        post = sub[(sub.ordered_at >= start) & (sub.ordered_at < start + pd.DateOffset(years=2))]
        before += pre.amount.sum()
        after += post.amount.sum()
        n_before += len(pre)
        n_after += len(post)
        if pre.empty and not post.empty:
            cold_start += post.amount.sum()
        if pre.empty and post.empty:
            silent += 1
    return before, after, n_before, n_after, cold_start, silent


def before_and_after(orders: pd.DataFrame, holders: pd.DataFrame, draws: int = 20) -> None:
    """Does a vendor's ordering change when it wins its first vehicle?

    The vendor route is only worth anything if holding a vehicle is what brings
    the orders. This compares the 24 months before a vendor's first vehicle
    start against the 24 months after, for the vendors whose first vehicle
    started early enough and late enough that both windows sit inside the
    purchase-order data.

    A ratio above 1 on its own means nothing, because IT purchase-order dollars
    grew over the same period anyway. The placebo repeats the exercise on
    vendors who never held a vehicle, dated with the same award dates drawn at
    random. If the real ratio sits inside the placebo spread, winning a vehicle
    is not visible in the ordering, and the vendor-name route is measuring the
    pre-existing relationship rather than the vehicle.
    """
    window = holders[
        pd.to_datetime(holders.first_start).between(
            pd.Timestamp("2020-01-01"), pd.Timestamp("2024-06-30")
        )
    ]
    dates = pd.to_datetime(window.first_start).tolist()
    pairs = list(zip(window.index, dates))
    before, after, n_before, n_after, cold_start, silent = _straddle(orders, pairs)
    print(f"vendors whose first vehicle started 2020-01 to 2024-06: {len(window)}")
    print(f"  24 months before that award: {n_before:,} orders, {money(before)}")
    print(f"  24 months after  that award: {n_after:,} orders, {money(after)}")
    print(f"  ratio after/before: {after / before:.2f}")
    print(f"  of the after-award dollars, from vendors with no orders before: {money(cold_start)}")
    print(f"  vendors with no IT orders on either side: {silent}")

    # Seeded so the published spread is reproducible; the draw is only a
    # yardstick for the ratio above, not an estimate of anything itself.
    rng = np.random.default_rng(0)
    others = [
        key for key in orders.vendor_key.dropna().unique()
        if key and key not in set(holders.index)
    ]
    ratios = []
    for _ in range(draws):
        picked = rng.choice(others, size=min(len(window), len(others)), replace=False)
        dated = [dates[i] for i in rng.integers(0, len(dates), len(picked))]
        pre, post, *_ = _straddle(orders, list(zip(picked, dated)))
        if pre > 0:
            ratios.append(post / pre)
    ratios.sort()
    mid = ratios[len(ratios) // 2]
    print(f"  placebo, {len(ratios)} draws of the same number of vendors who never held a "
          f"vehicle, dated with the same award dates:")
    print(f"    ratio after/before: median {mid:.2f}, range {ratios[0]:.2f} to {ratios[-1]:.2f}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    orders = parse.load_purchase_orders()
    payments = parse.load_payments()
    contracts = parse.load_contracts()
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")

    vehicles_df = vehicle_set(labels)
    vehicles = set(vehicles_df.contract_number)
    published = set(labels.contract_number)
    print(f"ordering vehicles: {len(vehicles_df)}, ceilings {money(vehicles_df.ceiling.sum())}")

    # Start dates come from the option-period rows, one contract per group.
    # End dates are deliberately not used -- see the module docstring.
    starts = contracts[contracts.contract_number.isin(vehicles)].groupby(
        "contract_number"
    ).start_date.min()
    vehicles_df["first_start"] = vehicles_df.contract_number.map(starts)

    frame = attribute_orders(orders, payments)
    frame["ordered_at"] = pd.to_datetime(frame.ordered_date)
    print(f"\npurchase orders reaching a payment row: {int(frame.in_payments.sum()):,} "
          f"of {len(frame):,}")
    coverage = frame.groupby("fiscal_year").in_payments.mean()
    print("  match rate by fiscal year: "
          + ", ".join(f"FY{int(y)} {r:.0%}" for y, r in coverage.items()))

    print("\nvendor identity")
    order_keys = set(orders.vendor.dropna().map(parse.normalize_vendor))
    order_keys.discard("")
    frame["vendor_key"] = frame.vendor.map(parse.normalize_vendor)
    vehicles_df["vendor_key"] = vendor_keys(vehicles_df.vendor, order_keys)
    holders = (
        vehicles_df[vehicles_df.vendor_key != ""]
        .groupby("vendor_key")
        .agg(first_start=("first_start", "min"), vehicles=("contract_number", "size"),
             ceiling=("ceiling", "sum"))
    )
    print(f"  {len(vehicles_df)} vehicles held by {len(holders)} distinct vendors")

    frame["attribution"] = frame.booked_to.map(lambda b: classify(b, vehicles, published))
    frame["vehicle"] = frame.booked_to.map(lambda b: which_vehicle(b, vehicles))
    frame["holds_a_vehicle"] = frame.vendor_key.isin(set(holders.index))
    held_start = frame.vendor_key.map(holders.first_start)
    frame["after_vendors_first_vehicle"] = frame.holds_a_vehicle & (
        frame.ordered_at >= pd.to_datetime(held_start)
    )

    window = frame[frame.fiscal_year.between(FY_LO, FY_HI)].copy()
    total = window.amount.sum()
    print(f"\nFY{FY_LO}-{FY_HI} IT purchase orders: {len(window):,}, {money(total)} committed")

    print("\nroute one, measured: what the payment record says these orders were booked against")
    table = window.groupby("attribution").agg(
        orders=("po_number", "size"), dollars=("amount", "sum")
    ).sort_index()
    for name, row in table.iterrows():
        print(f"  {name:<40} {int(row.orders):>6,} orders  {money(row.dollars):>10}"
              f"  {100 * row.dollars / total:>5.1f}%")

    traced = window[window.attribution == "A_traced_to_a_vehicle"]
    floor = traced.amount.sum()
    print(f"\n  traced to one of the {len(vehicles_df)} vehicles: {len(traced):,} orders, "
          f"{money(floor)}")
    per_vehicle = traced.groupby("vehicle").agg(
        orders=("po_number", "size"), dollars=("amount", "sum"),
        agencies=("agency", "nunique")
    ).sort_values("dollars", ascending=False)
    print(f"  vehicles with any traced order: {len(per_vehicle)} of {len(vehicles_df)}")
    top = per_vehicle.iloc[0]
    print(f"  largest, {per_vehicle.index[0]}: {money(top.dollars)} "
          f"({100 * top.dollars / floor:.1f}% of the traced total), "
          f"{int(top.agencies)} ordering agencies")
    rest = traced[traced.vehicle != per_vehicle.index[0]]
    print(f"  every other vehicle combined: {len(rest):,} orders, {money(rest.amount.sum())}")

    print("\nroute two, inferred: orders to vendors who hold a vehicle")
    holder_orders = window[window.holds_a_vehicle]
    after = window[window.after_vendors_first_vehicle]
    print(f"  to a vehicle holder, any date: {len(holder_orders):,} orders, "
          f"{money(holder_orders.amount.sum())} ({100 * holder_orders.amount.sum() / total:.1f}% "
          f"of IT order dollars)")
    print(f"  ...ordered after that vendor's first vehicle started: {len(after):,} orders, "
          f"{money(after.amount.sum())}")
    elsewhere = after[after.attribution.str.startswith("B_")]
    ceiling = after.amount.sum() - elsewhere.amount.sum()
    print(f"  of those, measurably booked against something that is not a vehicle: "
          f"{money(elsewhere.amount.sum())}")
    print(f"\n  bound on order dollars flowing through technology vehicles, FY{FY_LO}-{FY_HI}:")
    print(f"    {money(floor)} traced  to  {money(ceiling)} not excluded")

    print("\ndoes winning a vehicle change a vendor's ordering?")
    before_and_after(frame, holders)

    per_vehicle_out = vehicles_df.set_index("contract_number")[
        ["title", "vendor", "agency", "ceiling", "first_start", "vendor_key"]
    ].copy()
    per_vehicle_out["traced_orders"] = per_vehicle_out.index.map(
        per_vehicle.orders).fillna(0).astype(int)
    per_vehicle_out["traced_order_dollars"] = per_vehicle_out.index.map(
        per_vehicle.dollars).fillna(0.0)
    holder_after = after.groupby("vendor_key").agg(
        orders=("po_number", "size"), dollars=("amount", "sum")
    )
    per_vehicle_out["holder_orders_after_award"] = per_vehicle_out.vendor_key.map(
        holder_after.orders).fillna(0).astype(int)
    per_vehicle_out["holder_order_dollars_after_award"] = per_vehicle_out.vendor_key.map(
        holder_after.dollars).fillna(0.0)
    per_vehicle_out.to_csv(OUT / "vehicle_order_activity.csv")

    dark = per_vehicle_out[per_vehicle_out.holder_orders_after_award == 0]
    print(f"\nvehicles whose holder placed no IT purchase order at all after the vehicle "
          f"started: {len(dark)}, {money(dark.ceiling.sum())} of authorised ceiling")

    window[[
        "po_number", "po_base", "agency", "vendor", "vendor_key", "amount", "nigp_class",
        "group", "fiscal_year", "ordered_date", "in_payments", "attribution", "vehicle",
        "holds_a_vehicle", "after_vendors_first_vehicle",
    ]].assign(
        booked_to=window.booked_to.map(lambda b: ";".join(b) if isinstance(b, tuple) else "")
    ).to_csv(OUT / "po_contract_attribution.csv", index=False, quoting=csv.QUOTE_MINIMAL)
    print(f"\nwrote {OUT / 'po_contract_attribution.csv'}")
    print(f"wrote {OUT / 'vehicle_order_activity.csv'}")


if __name__ == "__main__":
    main()
