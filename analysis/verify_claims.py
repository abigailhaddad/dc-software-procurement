#!/usr/bin/env python3
"""Recompute every number used in the briefing, from the raw snapshot.

Each claim prints with the figure it asserts and the figure the data gives, so a
mismatch is visible rather than something a reader has to go hunting for. Run
this before publishing anything.

    uv run python analysis/verify_claims.py
    uv run python analysis/verify_claims.py --json out.json

The asserted figures are literals on purpose: they are what the briefing, the
site and the README currently say. They are the published baseline, and moving
one is a decision a person makes after looking at why it moved, not something a
script does. Fresh data will therefore make this fail, which is the point when
publishing and unhelpful when running on a schedule -- analysis/drift_report.py
reuses the same checks and reports movement instead of failing on it.

``--json`` writes one record per check, in the order the checks run, so a report
built from two runs is diffable. Nothing about the default run changes: same
lines on stdout, same non-zero exit when a claim no longer holds.

Around two dozen claims name one particular row -- contract CW91684's detail
record, the FY2021 payment total, a labor category on a rate card. The portal is
live and rows do leave it. Those lookups go through the helpers below rather
than through .loc / .iloc / next() directly, so a row that is gone fails its own
claim, says so in words, and does not take the rest of the run with it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse, vocabulary  # noqa: E402


@dataclass(frozen=True)
class Result:
    """One check: what was published, what the data gives now, and whether they agree."""

    index: int
    section: str
    claim: str
    asserted: object
    computed: object
    tolerance: float
    ok: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "section": self.section,
            "claim": self.claim,
            "asserted": _jsonable(self.asserted),
            "computed": _jsonable(self.computed),
            "tolerance": self.tolerance,
            "ok": self.ok,
        }


RESULTS: list[Result] = []
FAILURES: list[str] = []

# The section headers printed as the checks run double as the grouping in the
# JSON, so there is only one list of sections to keep current.
_SECTION = ""

# Set False by a caller that wants the results without the running commentary.
VERBOSE = True


def _echo(line: str) -> None:
    if VERBOSE:
        print(line)


def section(name: str) -> None:
    """Start a named group of checks, and print its header as before."""
    global _SECTION
    _SECTION = name
    _echo(f"\n-- {name} --")


def _jsonable(value: object) -> object:
    """Coerce a figure to something json can write.

    Most computed figures arrive as numpy scalars out of pandas, which json
    refuses. Anything left over is written as its string form rather than
    dropped, so a record is never silently empty.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    if isinstance(value, (int, float)):
        return value
    return str(value)


class MissingRow(LookupError):
    """A row a published claim names is not in the data any more.

    Raised only by the lookup helpers below, and only once they have tested
    that the row really is absent. :func:`check` catches this and nothing else,
    which is the whole point of it existing: a contract that has left the
    portal fails its own claim, while a KeyError from a column name that never
    existed, or a TypeError out of a broken expression, still stops the run
    where it happened.
    """

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


@dataclass(frozen=True)
class Missing:
    """Stands in for a figure whose row was not there to compute it from.

    Deliberately neither None nor zero. Both of those read as a figure the data
    gave, and "the contract was deleted" is a different finding from "the
    contract was paid nothing". It compares equal to nothing, so the claim
    fails, and it renders as its own sentence in stdout, in --json and in the
    drift report.
    """

    what: str

    def __str__(self) -> str:
        return f"<row not found: {self.what}>"


def nonempty(rows: object, what: str) -> object:
    """Rows, having established that there are some.

    min(), max() and median() of nothing raise, and the raise would take every
    check after it down with it.
    """
    if len(rows) == 0:
        raise MissingRow(what)
    return rows


def first(rows: object, what: str) -> object:
    """The first row, or index label, of something that may have matched nothing.

    Emptiness is tested rather than caught. An out-of-bounds ``.iloc[0]`` here
    only ever means the filter matched no rows; any other IndexError is a bug
    and should look like one.
    """
    rows = nonempty(rows, what)
    return rows.iloc[0] if hasattr(rows, "iloc") else rows[0]


def at(container: object, key: object, what: str) -> object:
    """One row by key, from a pandas Series/DataFrame or a plain mapping.

    The membership test is what keeps the failure honest: MissingRow is raised
    only after establishing the key is absent, so a KeyError thrown for some
    other reason is never dressed up as a vanished row.
    """
    pandas_like = isinstance(container, (pd.Series, pd.DataFrame))
    index = container.index if pandas_like else container
    if key not in index:
        raise MissingRow(what)
    return container.loc[key] if pandas_like else container[key]


def present(value: object, what: str) -> object:
    """A value the data may simply not carry, such as an award document.

    documents.pick_award_document returns None for a contract record that lists
    no award, and a None subscripted on the next line is a TypeError that says
    nothing about which contract lost its document.
    """
    if value is None:
        raise MissingRow(what)
    return value


def deferred(compute: Callable[[], object]) -> Callable[[], object]:
    """Memoize a shared lookup while leaving each check its own failure.

    A block like the CW91684 rate card reads one row and asks eight questions
    of it. Doing the lookup once and re-raising the same MissingRow on every
    call means the work happens once and all eight claims are still recorded as
    failures when the row is gone, instead of the first one failing and the
    other seven never running.
    """
    box: list[tuple[bool, object]] = []

    def get() -> object:
        if not box:
            try:
                box.append((True, compute()))
            except MissingRow as exc:
                box.append((False, exc))
        found, value = box[0]
        if not found:
            raise value
        return value

    return get


def check(claim: str, computed: object, asserted: object, tolerance: float = 0.0) -> None:
    """Compare an asserted figure against what the data actually gives.

    ``computed`` may instead be a zero-argument callable, for a figure that
    reaches into one particular row. The portal is live and rows do leave it,
    so the lookup helpers above raise MissingRow and this is where it is
    caught: the claim is recorded as a failure naming the row that went
    missing, and the checks after it still run. MissingRow is the only
    exception caught here; every computed figure in this file is a number, a
    string or a bool, so nothing else is ever mistaken for a lookup.
    """
    if callable(computed):
        try:
            computed = computed()
        except MissingRow as exc:
            computed = Missing(exc.what)
    if isinstance(computed, (int, float)) and isinstance(asserted, (int, float)):
        ok = abs(float(computed) - float(asserted)) <= tolerance
    else:
        ok = computed == asserted
    mark = "ok  " if ok else "FAIL"
    _echo(f"  {mark} {claim}\n         asserted={asserted}  computed={computed}")
    RESULTS.append(Result(len(RESULTS), _SECTION, claim, asserted, computed, tolerance, ok))
    if not ok:
        FAILURES.append(claim)


def run_checks() -> list[Result]:
    """Run every check and return the results, one record per claim.

    A claim about a row that has since left the data fails on its own line and
    the run carries on; the lookup helpers above are how. Anything else raises,
    which is what a genuine bug should do -- see drift_report.py, which is the
    caller that has to survive that.
    """
    RESULTS.clear()
    FAILURES.clear()
    contracts = parse.load_contracts()
    orders = parse.load_purchase_orders()
    payments = parse.load_payments()
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    delivery = pd.read_csv(ROOT / "data" / "delivery_structure.csv")

    section("corpus")
    check("contract option periods", len(contracts), 7169)
    check("distinct contracts", contracts.contract_number.nunique(), 4013)
    check("contracts classified", len(labels), 4013)
    check("purchase orders (IT classes)", len(orders), 114435)
    check("payments", len(payments), 869839)
    check("earliest PO fiscal year", int(orders.fiscal_year.min()), 2013)

    # The manifest is the provenance record. If it stops describing the files
    # on disk, rebuild it: uv run python analysis/rebuild_manifest.py
    import json as _json

    manifest = _json.load(open(ROOT / "data" / "raw" / "manifest.json"))
    recorded = {s["name"]: s["rows"] for s in manifest["snapshots"]}
    check("manifest matches contracts on disk", recorded.get("contracts_by_commodity"), len(contracts))
    check("manifest matches purchase orders on disk", recorded.get("purchase_by_commodity"), len(orders))
    check("manifest matches payments on disk", recorded.get("payments"), len(payments))

    section("what DC buys")
    tech = labels[labels.is_technology == True]  # noqa: E712
    check("technology contracts", len(tech), 1383)
    check("technology ceilings $M", round(tech.ceiling.sum() / 1e6), 4670, 1)
    for work_type, contracts_n, ceiling_m in [
        ("ordering_vehicle", 330, 1851),
        ("staff_augmentation", 213, 1208),
        ("custom_software_build", 62, 39),
    ]:
        rows = labels[labels.work_type == work_type]
        check(f"{work_type} contracts", len(rows), contracts_n)
        check(f"{work_type} ceilings $M", round(rows.ceiling.sum() / 1e6), ceiling_m, 1)

    section("ceilings are not spend")
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
        ("firm fixed price drawdown %", "Firm Fixed Price", 45),
    ]:
        rows = tech[tech.contract_type == ctype]
        check(label, round(100 * rows.paid.sum() / rows.ceiling.sum()), pct, 1)
    check("technology contracts with any linked payment %",
          round(100 * (tech.paid > 0).mean()), 44, 1)

    section("the payments trap")
    check("line-level total $B", round(payments.amount.sum() / 1e9, 1), 33.5, 0.1)
    check("check-total field $B", round(payments.check_total.sum() / 1e9, 1), 114.7, 0.1)
    star = payments[payments.contract_number == "CW95481"]
    check("CW95481 correct $M", round(star.amount.sum() / 1e6), 132, 1)
    check("CW95481 if summed wrong $B", round(star.check_total.sum() / 1e9, 1), 17.4, 0.2)
    # The portal is live: counts move a little between pulls, so these carry a
    # tolerance rather than pretending to be exact.
    #
    # 481,031 is what the briefing, the site and the README actually say. The
    # literal here was once rounded to the nearest hundred, and the tolerance of
    # 200 hid the difference -- so this check passed against a number nothing
    # published. The tolerance is for portal movement, not for the baseline
    # being approximate.
    check("payments with no contract number",
          int(payments.contract_number.isna().sum()), 481031, 200)

    section("agile language")
    build_types = {"custom_software_build", "cots_implementation",
                   "software_om_support", "it_consulting_advisory"}
    candidates = int(labels.work_type.isin(build_types).sum())
    check("delivery-structure candidates", candidates, 480)
    check("readable award documents", len(delivery), 313)
    mentions = delivery[delivery.agile_mention_kind != "no_agile_language"]
    check("documents mentioning agile", len(mentions), 44)
    commits = delivery[delivery.agile_mention_kind == "delivery_commitment"]
    check("genuine delivery commitments", len(commits), 14)
    check("mentions that are staff qualifications",
          int((delivery.agile_mention_kind == "personnel_qualification").sum()), 17)
    # What used to sit here -- how many of the agile commitments are also
    # structurally gated, and the gated rate among everything else -- was a
    # cross-tab on delivery_structure, and is not published any more. The field
    # agrees with blind hand labels 57% of the time, so a cross-tab on it is
    # noise crossed with a finding.

    # Two of the eleven commitments are hand-read corrections of classifier
    # misses. The overrides file must stay in agreement with the gold labels,
    # and every override must actually be applied in the published data.
    import yaml as _yaml

    overrides = _yaml.safe_load((ROOT / "delivery_overrides.yaml").read_text(encoding="utf-8"))
    corrected = overrides.get("agile_mention_kind") or {}
    check("hand-corrected commitment rows", len(corrected), 2)
    gold = pd.read_csv(ROOT / "tests" / "delivery_gold_labels.csv").set_index("contract_number")
    published = delivery.set_index("contract_number")
    for contract_number, spec in corrected.items():
        check(f"override {contract_number} applied in published data",
              lambda cn=contract_number: at(
                  published, cn, f"contract {cn} in data/delivery_structure.csv"
              ).agile_mention_kind,
              spec["value"])
        check(f"override {contract_number} recorded in gold labels",
              lambda cn=contract_number, want=spec["value"]: want in str(at(
                  gold, cn, f"contract {cn} in tests/delivery_gold_labels.csv"
              ).agile_mention_kind).split("|"),
              True)

    section("vendor concentration (payments to technology contracts, FY2021-2025)")
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
    top_vendor = "any vendor in FY2021-25 payments to technology contracts"
    check("largest vendor is Computer Aid",
          lambda: first(ranked.index, top_vendor), "COMPUTER AID")
    check("largest vendor paid $M",
          lambda: round(first(ranked, top_vendor) / 1e6, 1), 100.6, 0.5)

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

    section("the DC Supply Schedule")
    dcss = tech[tech.title.str.contains("DCSS", case=False, na=False)]
    check("technology contracts naming DCSS", len(dcss), 399, 3)
    check("their ceilings $M", round(dcss.ceiling.sum() / 1e6), 2412, 5)
    check("share of technology ceilings %",
          round(100 * dcss.ceiling.sum() / tech.ceiling.sum()), 52, 1)
    vehicles = tech[tech.work_type == "ordering_vehicle"]
    check("ordering vehicles that are DCSS",
          int(vehicles.title.str.contains("DCSS", case=False, na=False).sum()), 306, 3)
    # The $2.4B mixes pool-vehicle ceilings with DCSS-named awards for actual
    # work; the briefing states the split, so it is checked here.
    non_vehicle = dcss[dcss.work_type != "ordering_vehicle"]
    check("DCSS-named contracts that are not vehicles", len(non_vehicle), 93, 2)
    check("their ceilings $M", round(non_vehicle.ceiling.sum() / 1e6), 845, 5)
    check("of them, staff augmentation",
          int((non_vehicle.work_type == "staff_augmentation").sum()), 76, 2)

    section("payments against unpublished contract numbers")
    # The evidence that awards exist beyond what the portal publishes: money
    # moves under portal-style CW numbers that appear nowhere in the published
    # contract list. Some may have been published once and later removed;
    # either way a reader of the portal today cannot look them up.
    recent_pay = payments[payments.fiscal_year.between(2021, 2025)]
    cw_pay = recent_pay[recent_pay.contract_number.astype(str).str.match(r"^CW\d+$", na=False)]
    unpublished = cw_pay[~cw_pay.contract_number.isin(set(contracts.contract_number))]
    check("distinct CW numbers paid FY2021-25", cw_pay.contract_number.nunique(), 3079, 10)
    check("of them, absent from the published list",
          unpublished.contract_number.nunique(), 1471, 10)
    check("payments against absent numbers $B",
          round(unpublished.amount.sum() / 1e9, 1), 0.8, 0.05)

    section("traceable technology payments by year")
    tech_pay = payments[
        payments.contract_number.isin(set(tech.contract_number))
        & payments.fiscal_year.between(2021, 2025)
    ]
    by_year = tech_pay.groupby("fiscal_year").amount.sum() / 1e6
    for year, want in [(2021, 58), (2025, 175)]:
        check(f"traceable technology payments FY{year} $M",
              lambda y=year: round(at(
                  by_year, y, f"fiscal year {y} in traceable technology payments"
              )), want, 2)

    section("payments outside the ceiling window")
    # Payments reach back to FY2017 while contract option periods start in
    # October 2020, so a whole-history comparison counts money paid before any
    # ceiling in this data existed.
    check("earliest payment fiscal year", int(payments.fiscal_year.min()), 2017)
    starts = pd.to_datetime(contracts.start_date, errors="coerce")
    check("earliest option period start", str(starts.min().date()), "2020-10-01")
    pre = payments[payments.fiscal_year < 2021]
    check("payments before FY2021 $B", round(pre.amount.sum() / 1e9, 1), 11.9, 0.1)

    section("named systems")
    named = tech[tech.system_name.notna() & (tech.system_name.astype(str).str.strip() != "")]
    named = named[named.system_name.astype(str).str.lower() != "nan"]
    check("technology contracts naming a system", len(named), 276)
    by_system = named.groupby(named.system_name.astype(str).str.upper().str.strip()).agg(
        contracts=("contract_number", "size"), ceiling=("ceiling", "sum")
    ).sort_values("ceiling", ascending=False)
    top_system = "any named system among technology contracts"
    check("largest named system is DCAS",
          lambda: first(by_system.index, top_system), "DCAS")
    check("DCAS contracts",
          lambda: int(first(by_system, top_system).contracts), 28, 1)
    check("DCAS ceilings $M",
          lambda: round(first(by_system, top_system).ceiling / 1e6, 1), 198.3, 1.0)

    section("delivery structure")
    # No count of delivery structures is published any more. What is published
    # is why: the field cannot be read out of most of these documents, and the
    # blind hand labels are the evidence for that. hand.evidence_basis records
    # what each hand label actually rested on -- the four values below are the
    # published sentence, in order.
    hand = pd.read_csv(ROOT / "tests" / "hand_labels_delivery.csv")
    check("blind hand labels read", len(hand), 80)
    basis = hand.evidence_basis.value_counts()
    check("labels resting on an explicit process description",
          lambda: int(at(basis, "explicit_process", "evidence_basis explicit_process")), 25)
    check("labels resting on the price schedule alone",
          lambda: int(at(basis, "pricing_structure_only",
                         "evidence_basis pricing_structure_only")), 25)
    check("labels resting on what the work plainly is",
          lambda: int(at(basis, "scope_description", "evidence_basis scope_description")), 25)
    check("labels resting on a deliverables table alone",
          lambda: int(at(basis, "deliverables_table_only",
                         "evidence_basis deliverables_table_only")), 4)
    check("labels resting on nothing",
          lambda: int(at(basis, "none", "evidence_basis none")), 1)

    # Two readers on the same 30 contracts. This is the ceiling the classifier
    # is measured against, not 100%.
    rater2 = pd.read_csv(ROOT / "tests" / "hand_labels_delivery_rater2.csv")
    pair = hand.merge(rater2, on="contract_number", suffixes=("", "_2"))
    check("contracts both readers labelled", len(pair), 30)
    check("inter-rater agreement %",
          round(100 * (pair.delivery_structure == pair.delivery_structure_2).mean()), 83, 1)

    # And the classifier against those hand labels. 57% here rather than the
    # 55% in docs/CLASSIFIER_ACCURACY.md: that figure is the random sample
    # alone, this one is all 80, and the stratified half scored better. See the
    # note in that file.
    model_labels = pd.concat(
        [delivery, pd.read_csv(ROOT / "data" / "delivery_structure_ocr.csv")],
        ignore_index=True,
    ).drop_duplicates("contract_number").set_index("contract_number")
    scored = hand.assign(
        model=hand.contract_number.map(model_labels.delivery_structure)
    )
    check("hand-labelled contracts the classifier also labelled",
          int(scored.model.notna().sum()), 80)
    check("classifier agreement with the hand labels %",
          round(100 * (scored.delivery_structure == scored.model).mean()), 57, 1)

    check("agile only as a hiring requirement",
          int((delivery.agile_mention_kind == "personnel_qualification").sum()), 17)

    section("modifications")
    # analysis/modifications.py writes both of these. The counting trap is in
    # its docstring: 24,471 attachment rows are 8,547 documents, and the
    # duplicates are byte-identical.
    mods = pd.read_csv(ROOT / "data" / "modifications.csv")
    actions = pd.read_csv(ROOT / "data" / "modification_actions.csv")
    details_raw = _json.load(open(ROOT / "data" / "raw" / "contract_details.json"))
    mod_rows = sum(
        1
        for record in details_raw
        for document in record.get("documents") or []
        if (document.get("name") or "").split("-")[2:3] == ["Modifications"]
    )
    check("modification attachment rows the portal lists", mod_rows, 24471, 200)
    mod_names = {
        (record["contractNumber"], "-".join((document.get("name") or "").split("-")[3:]))
        for record in details_raw
        for document in record.get("documents") or []
        if (document.get("name") or "").split("-")[2:3] == ["Modifications"]
    }
    check("distinct modification documents", len(mod_names), 8547, 100)
    check("contracts carrying at least one", len({c for c, _ in mod_names}), 1609, 20)

    # The confound, stated as the published figures state it.
    by_periods = mods.groupby("option_periods").modifications.mean()
    check("mean modifications, one option-period row",
          lambda: round(float(at(by_periods, 1, "contracts with one option period")), 1), 1.3, 0.05)
    check("mean modifications, three option-period rows",
          lambda: round(float(at(by_periods, 3, "contracts with three option periods")), 1),
          5.3, 0.05)

    # The comparison, conditioned on having had the chance to be modified.
    conditioned = mods[(mods.option_periods >= 2) & (mods.modifications > 0)]
    check("build contracts in the conditioned comparison", len(conditioned), 121, 2)
    fixed = conditioned[conditioned.fixed_scope]
    rest = conditioned[~conditioned.fixed_scope]
    check("of those, scope fixed in advance", len(fixed), 15, 1)
    check("their mean modifications", round(fixed.modifications.mean(), 1), 6.5, 0.1)
    check("everything else", len(rest), 106, 2)
    check("its mean modifications", round(rest.modifications.mean(), 1), 4.7, 0.1)

    # What the modifications do. Coverage is reported, not assumed: an
    # unreadable modification is unread, never "no scope change".
    check("modification documents read", len(actions), 400)
    outcomes = actions.outcome.value_counts()
    check("of those, legible with an amendment block",
          lambda: int(at(outcomes, "read", "readable modification documents")), 279, 5)
    check("scans with no text layer",
          lambda: int(at(outcomes, "scanned, no text layer", "scanned modifications")), 76, 5)
    check("text layers that extract as mojibake",
          lambda: int(at(outcomes, "text layer is mojibake", "mojibake modifications")), 12, 3)
    check("readable but no amendment block",
          lambda: int(at(outcomes, "no block 14 found", "modifications with no block 14")), 33, 5)
    legible = actions[actions.outcome == "read"]
    check("exercise an option or extend the term",
          int(legible.option_or_extension.sum()), 145, 3)
    check("adjust a living wage rate", int(legible.wage_adjustment.sum()), 86, 3)
    check("change a contracting officer or requisition",
          int(legible.administrative.sum()), 60, 3)
    check("move money", int(legible.funding_change.sum()), 35, 3)
    changed = legible.scope_change | legible.schedule_change
    check("change what is built or when it is due", int(changed.sum()), 27, 2)
    check("of those, on fixed-scope contracts",
          int(changed[legible.fixed_scope].sum()), 9, 1)
    check("legible modifications on fixed-scope contracts",
          int(legible.fixed_scope.sum()), 61, 3)

    section("authorised against paid, FY2021-FY2025 window")
    # The briefing compares ceilings and payments over the same five fiscal
    # years: option periods falling entirely inside FY2021-FY2025 against
    # payments made in those years. Without the window the comparison counts
    # payments made before a ceiling existed against authorisation nobody
    # could have drawn yet.
    cwin = contracts[contracts.contract_number.isin(set(tech.contract_number))].copy()
    cwin["start"] = pd.to_datetime(cwin.start_date, errors="coerce")
    cwin["end"] = pd.to_datetime(cwin.end_date, errors="coerce")
    lo, hi = pd.Timestamp("2020-10-01"), pd.Timestamp("2025-09-30")
    inwin = cwin[(cwin.start >= lo) & (cwin.end <= hi)]
    win_paid = payments[payments.fiscal_year.between(2021, 2025)].groupby("contract_number")["amount"].sum()
    g = inwin.groupby("contract_number").agg(ceiling=("amount", "sum"), ctype=("contract_type", "first"))
    g["paid"] = g.index.map(win_paid).fillna(0.0)
    check("windowed technology contracts", len(g), 1070)
    check("windowed ceilings $M", round(g.ceiling.sum() / 1e6), 3095, 2)
    check("windowed paid $M", round(g.paid.sum() / 1e6), 527, 2)
    check("windowed drawdown %", round(100 * g.paid.sum() / g.ceiling.sum(), 1), 17.0, 0.2)
    for ctype, n, ceil_m, paid_m, pct in [
        ("Indefinite Delivery Indefinite Quantity", 359, 1995, 56, 2.8),
        ("Firm Fixed Price", 575, 669, 313, 46.8),
        ("Labor Hour", 97, 394, 130, 33.0),
    ]:
        rows = g[g.ctype == ctype]
        check(f"windowed {ctype}: contracts", len(rows), n)
        check(f"windowed {ctype}: ceilings $M", round(rows.ceiling.sum() / 1e6), ceil_m, 2)
        check(f"windowed {ctype}: paid $M", round(rows.paid.sum() / 1e6), paid_m, 2)
        check(f"windowed {ctype}: drawdown %", round(100 * rows.paid.sum() / rows.ceiling.sum(), 1), pct, 0.2)

    section("the CW91684 rate card")
    # The staffing-contract section quotes the price schedule of Computer Aid's
    # CW91684. Its award PDF is cached locally, so the quoted figures can be
    # re-read from the document text itself.
    import gzip
    import json as _json
    import re
    import statistics

    from dcprocure import documents

    details = _json.load(open(ROOT / "data" / "raw" / "contract_details.json"))
    det_by_cn = {d["contractNumber"]: d for d in details}

    def detail(contract_number: str) -> object:
        """One contract's detail record, by contract number."""
        return at(det_by_cn, contract_number,
                  f"contract {contract_number} in data/raw/contract_details.json")

    def award_text(contract_number: str) -> str:
        """The cached text of one contract's award document."""
        doc = present(documents.pick_award_document(detail(contract_number)),
                      f"an award document on contract {contract_number}")
        return gzip.open(documents._text_path(doc["id"]), "rt").read()

    rec = deferred(lambda: detail("CW91684"))
    check("CW91684 vendor", lambda: rec()["vendor"], "COMPUTER AID, INC.")
    check("CW91684 title", lambda: rec()["title"],
          "INFORMATION TECHNOLOGY STAFF AUGMENTATION SERVICES")
    check("CW91684 ceiling $M",
          lambda: round(float(rec()["contractAmount"].strip("$").replace(",", "")) / 1e6), 65)
    text = deferred(lambda: award_text("CW91684"))
    for needle in ("45,000,000", "20,000,000", "Labor Hour", "Fixed Price Project"):
        check(f"CW91684 award text contains '{needle}'", lambda n=needle: n in text(), True)

    def cw91684_rates() -> list[float]:
        """Every hourly rate in CW91684's award text, in the range the card covers."""
        found = [float(x) for x in re.findall(r"\b(\d{2,3}\.\d{2})\b", text())]
        return [x for x in found if 24 <= x <= 174]

    # The count is reported as it stands, including zero. The three figures
    # after it are min/max/median, which have nothing to report over no rates
    # at all, so they say so rather than raising.
    rates = deferred(cw91684_rates)
    any_rate = "any hourly rate in CW91684's award text"
    check("CW91684 hourly rates found", lambda: len(rates()), 180)
    check("CW91684 lowest rate", lambda: min(nonempty(rates(), any_rate)), 24.01)
    check("CW91684 highest rate", lambda: max(nonempty(rates(), any_rate)), 173.71)
    check("CW91684 median rate",
          lambda: round(statistics.median(nonempty(rates(), any_rate)), 2), 69.97)

    section("published hourly rate cards")
    # Built by analysis/extract_rates.py: every rate re-found verbatim in the
    # award text; the pairing column records whether the category-to-rate
    # assignment could also be verified (deterministic reconstruction for
    # CW91684, text proximity for row-oriented tables).
    lr = pd.read_csv(ROOT / "data" / "labor_rates.csv")
    check("contracts publishing hourly rates", lr.contract_number.nunique(), 60)
    check("published hourly rates", len(lr), 2581)
    check("rates with verified category pairing",
          int(lr.pairing.isin(["deterministic", "proximate"]).sum()), 1737)
    check("lowest published rate", float(lr.hourly_rate.min()), 23.68)
    check("highest published rate", float(lr.hourly_rate.max()), 1015.0)
    check("median published rate", float(lr.hourly_rate.median()), 142.78, 0.5)
    # 325 rates come from OCR-recovered scans; their verification is against
    # the OCR's reading, one model layer from the pixels, and every one of
    # them carries a proximity-verified pairing.
    ocr_rates = lr[lr.text_source == "ocr"]
    check("rates from OCR-recovered scans", len(ocr_rates), 325)
    check("all OCR rates pairing-verified", bool((ocr_rates.pairing == "proximate").all()), True)
    check("native rows unchanged", int((lr.text_source == "native").sum()), 2256)
    cai_card = lr[lr.contract_number == "CW91684"].set_index(["labor_category", "level"])

    def card_rate(cat: str, lvl: str) -> float:
        """One published rate off CW91684's card, by category and level."""
        return float(at(cai_card, (cat, lvl),
                        f"{cat} ({lvl}) on CW91684's rate card in "
                        "data/labor_rates.csv").hourly_rate)

    for cat, lvl, want in [
        ("Enterprise Architect", "Entry", 75.83),
        ("Web Developer", "Master", 77.00),
        ("Mobile App Developer", "Entry", 70.76),
        ("Cyber Security Engineer", "Master", 143.42),
    ]:
        check(f"CW91684 {cat} ({lvl})", lambda c=cat, v=lvl: card_rate(c, v), want)

    # The disclosure contrast the briefing states: the largest consultancies'
    # rates are not published.
    check("KPMG CW62227 has no award document",
          lambda: documents.pick_award_document(detail("CW62227")) is None, True)
    check("Ridge IT CW115647 has no award document",
          lambda: documents.pick_award_document(detail("CW115647")) is None, True)
    check("Accenture CW67170 award text is a stub (chars)",
          lambda: len(award_text("CW67170")), 41)

    # The billed-vs-offered comparison quotes job postings. Those pages die, so
    # every quoted posting must be preserved in evidence/job_ads/ with its pay
    # text recorded in the manifest exactly as the briefing states it.
    manifest_text = (ROOT / "evidence" / "job_ads" / "manifest.csv").read_text(encoding="utf-8")
    for needle in ("$74.00 - $75.00 per hour", "$54 - $59 per hour", "$40 - $45 per hour",
                   "$20 - $23 per hour", "$91,000.00 - USD $96,000.00", "$80 - $90/hr"):
        check(f"posting evidence preserved: '{needle}'", needle in manifest_text, True)
    for cat, lvl, want in [("Customer Support", "Senior", 29.31),
                           ("Technical Writer", "Journeyman", 57.35)]:
        check(f"CW91684 {cat} ({lvl}) vs posting",
              lambda c=cat, v=lvl: card_rate(c, v), want)

    section("federal rate comparison (CALC+)")
    # DC rates matched by exact title against GSA's published awarded ceiling
    # rates (data/calc/, with methods and raw API responses). The comparison
    # file is rebuilt by analysis/build_calc_comparison.py.
    calc = pd.read_csv(ROOT / "data" / "calc" / "dc_vs_calc_comparison.csv")
    check("matched DC rate rows", len(calc), 119)
    cai_rows = calc[calc.dc_contract == "CW91684"]
    check("of them, on the CW91684 card", len(cai_rows), 44)
    check("CW91684 median gap vs federal median %",
          round(cai_rows.dc_vs_fed_median_pct.median()), -50, 1)
    check("DC rows below the federal median",
          int((calc.dc_vs_fed_median_pct < 0).sum()), 73)
    check("median gap excluding CW91684 %",
          round(calc[calc.dc_contract != "CW91684"].dc_vs_fed_median_pct.median()), 2, 1)
    matched = "in data/calc/dc_vs_calc_comparison.csv"
    ne = calc[(calc.dc_category == "Network Engineer/Specialist") & (calc.dc_level == "Entry")]
    ne_row = f"Network Engineer/Specialist (Entry) {matched}"
    check("Network Engineer entry: DC rate",
          lambda: float(first(ne.dc_rate, ne_row)), 40.64)
    check("Network Engineer entry: federal median",
          lambda: float(first(ne.federal_median, ne_row)), 108.97)
    cs = calc[(calc.dc_category == "Computer Scientist") & (calc.dc_level == "Entry")]
    check("Computer Scientist entry: federal median",
          lambda: float(first(cs.federal_median, f"Computer Scientist (Entry) {matched}")), 146.08)

    section("solicitation visibility")
    # Whether each technology contract traces to any posted solicitation,
    # via award-text citation or title match (data/solicitations/, rebuilt by
    # the analysis/solicitations_* scripts). Matching recall is ~63%, so the
    # briefing states these as "at most about a third of contracts, about a
    # tenth of dollars" after correction — the raw figures are checked here.
    vis = pd.read_csv(ROOT / "data" / "solicitations" / "tech_contract_solicitation_visibility.csv")
    check("technology contracts assessed", len(vis), 1383)
    visible = vis[vis.visibility.str.startswith("visible")]
    check("trace to a posted solicitation", len(visible), 298)
    check("their share of tech ceilings %",
          round(100 * visible.ceiling.sum() / vis.ceiling.sum(), 1), 11.3, 0.2)
    fy = pd.read_csv(ROOT / "data" / "solicitations" / "solicitations_by_fy.csv")
    for year, want in [("FY2016", 1351), ("FY2025", 749)]:
        check(f"solicitations posted {year}",
              lambda y=year: int(first(
                  fy[fy.fiscal_year == y].total,
                  f"{y} in data/solicitations/solicitations_by_fy.csv"
              )), want)
    sols = _json.load(open(ROOT / "data" / "raw" / "solicitations.json"))
    doc_ids = sum(1 for s in sols if str(s.get("solicitationNumber", "")).startswith("Doc"))
    check("solicitation rows showing internal Doc IDs %",
          round(100 * doc_ids / len(sols)), 96, 1)

    section("the DCAS case study")
    # data/dcas/ holds the roster and attributed payments (rebuilt by
    # analysis/build_dcas.py); evidence/dcas_sources/ holds every cited page.
    roster = pd.read_csv(ROOT / "data" / "dcas" / "dcas_roster.csv")
    check("DCAS roster contracts", len(roster), 49)
    check("visible DCAS ceilings $M", round(roster.ceiling.sum() / 1e6), 249, 1)
    check("roster contracts with zero tagged payments",
          int((roster.payments_total.fillna(0) == 0).sum()), 19, 1)
    check("roster contracts paid above their surviving ceiling",
          int((roster.payments_total.fillna(0) > roster.ceiling).sum()), 7, 1)
    dcas_pay = pd.read_csv(ROOT / "data" / "dcas" / "dcas_payments_by_year.csv")
    check("payments tied to DCAS $M", round(dcas_pay.amount.sum() / 1e6), 291, 1)

    # The two claims a hostile reader will check first, recomputed from raw:
    kpmg_pay = payments[payments.contract_number == "CW62227"]
    check("KPMG paid citing DCAS R3's number $M", round(kpmg_pay.amount.sum() / 1e6), 50, 1)
    check("DCAS R3's surviving portal record: option periods",
          int((contracts.contract_number == "CW62227").sum()), 1)
    check("that surviving option period $M",
          lambda: round(first(
              contracts[contracts.contract_number == "CW62227"].amount,
              "contract CW62227 in the published contract list"
          ) / 1e6), 7, 1)
    kpmg_null = payments[
        payments.vendor.astype(str).str.upper().str.startswith("KPMG")
        & payments.contract_number.isna()
        & payments.agency.astype(str).str.contains("Health Care Finance", na=False)
    ]
    check("KPMG null-contract payments at Health Care Finance $M",
          round(kpmg_null.amount.sum() / 1e6), 60, 1)
    d2sol_exact = payments[
        payments.vendor.astype(str).str.upper().str.contains("D2SOL", na=False)
        & (abs(payments.amount - 3490537.83) < 0.01)
    ]
    check("D2Sol monthly O&M vouchers at $3,490,537.83", len(d2sol_exact) >= 17, True)

    # Web-sourced figures must exist verbatim in the saved sources.
    henderson = (ROOT / "evidence" / "dcas_sources" / "henderson_joint_roundtable_dcas.txt").read_text(encoding="utf-8")
    check("source: Council's 'more than $622 million'", "more than $622 million" in henderson, True)
    check("source: Deputy Mayor's 'emphatic' no", "emphatic" in henderson, True)
    check("source: FY2022 SNAP timeliness below half the 2017 lawsuit rate",
          "more than double the Fiscal Year 2022 rate" in henderson, True)
    infosys = (ROOT / "evidence" / "dcas_sources" / "infosyspublicservices_2013_hbx_press_release.txt").read_text(encoding="utf-8")
    check("source: Infosys $49.5M build award", "49.5" in infosys, True)
    # The DCAS workforce paragraph: Curam-titled H-1B wage filings at DC
    # worksites, recomputed from the saved aggregator page in evidence/h1b/.
    # (h1bdata.info aggregates DOL LCA disclosures; DOL is authoritative.)
    import re as _re

    curam_html = (ROOT / "evidence" / "h1b" / "job_curam_all.html").read_text(encoding="utf-8")
    curam_rows = _re.findall(r"<tr[^>]*>\s*<td.*?</tr>", curam_html, flags=_re.S)
    dc_rows = [r for r in curam_rows if _re.search(r"WASHINGTON,?\s*D\.?C?\b", r, _re.I)]
    check("Curam-titled LCA filings at DC worksites", len(dc_rows), 110)
    dc_employers = set()
    for r in dc_rows:
        tds = _re.findall(r"<td[^>]*>(.*?)</td>", r, flags=_re.S)
        name = _re.sub(r"<[^>]+>", "", tds[0]).strip().upper()
        dc_employers.add(_re.sub(r"\s+(INC|LLC)$", "", name))
    check("distinct employers filing them", len(dc_employers), 6)
    check("of them, D2Sol's filings",
          sum(1 for r in dc_rows if "D2SOL" in r.upper()), 89)
    check("eSystems' filings", sum(1 for r in dc_rows if "ESYSTEMS" in r.upper()), 15)
    for absent in ("DENOVO", "NETLINE"):
        check(f"{absent} absent from portal contracts and payments",
              int(labels.vendor.astype(str).str.upper().str.contains(absent, na=False).sum())
              + int(payments.vendor.astype(str).str.upper().str.contains(absent, na=False).sum()), 0)

    # DOL's own disclosure files (analysis/pull_h1b_dol.py) carry the client
    # agency in SECONDARY_ENTITY_BUSINESS_NAME; the briefing quotes them.
    dol = pd.read_csv(ROOT / "data" / "h1b" / "dol" / "lca_dol_filtered.csv")
    check("DOL-filtered LCA rows", len(dol), 1725)
    sec = dol[dol.SECONDARY_ENTITY_BUSINESS_NAME.notna()]
    esys_dcas = sec[
        sec.EMPLOYER_NAME.str.contains("ESYSTEMS", case=False, na=False)
        & sec.SECONDARY_ENTITY_BUSINESS_NAME.str.contains("DC ACCESS", case=False, na=False)
        & (sec.CASE_STATUS == "Certified")
    ]
    check("eSystems certified filings naming DCAS", len(esys_dcas), 7)
    leads = esys_dcas[esys_dcas.JOB_TITLE.str.contains("TEAM LEAD", case=False)]
    check("of them, senior Curam developer team leads", len(leads), 5)
    check("team-lead wage floor", float(leads.WAGE_RATE_OF_PAY_FROM.min()), 170000.0)
    check("team-lead wage top", float(leads.WAGE_RATE_OF_PAY_FROM.max()), 203000.0)
    d2_dhcf = sec[
        sec.EMPLOYER_NAME.str.contains("D2SOL", case=False, na=False)
        & sec.SECONDARY_ENTITY_BUSINESS_NAME.str.contains("Health Care Finance", case=False, na=False)
    ]
    check("D2Sol filings naming DHCF", len(d2_dhcf), 33)
    check("Avid filings naming OCTO",
          int(sec[sec.EMPLOYER_NAME.str.contains("AVID", case=False, na=False)]
              .SECONDARY_ENTITY_BUSINESS_NAME.str.contains("CHIEF TECHNOLOGY", case=False).sum()) >= 1, True)
    ct55 = dol[
        dol.EMPLOYER_NAME.str.contains("CHANGING", case=False, na=False)
        & (dol.WAGE_RATE_OF_PAY_FROM == 55.0) & (dol.WAGE_UNIT_OF_PAY == "Hour")
        & (dol.CASE_STATUS == "Certified")
        & dol.SECONDARY_ENTITY_BUSINESS_NAME.str.contains("DC Health", na=False)
    ]
    check("Changing Technologies $55/hour at DC Health, certified", len(ct55), 1)
    sriven = dol[
        dol.EMPLOYER_NAME.str.contains("SRIVEN", case=False, na=False)
        & (dol.WAGE_RATE_OF_PAY_FROM == 129999.0)
        & dol.SECONDARY_ENTITY_BUSINESS_NAME.str.contains("Health Care Finance", case=False, na=False)
    ]
    check("remote Curam developer for DHCF, certified", len(sriven), 1)

    # The h1b pipeline (analysis/pull_h1b.py, make h1b) must agree with the
    # saved evidence page the paragraph is checked against above.
    lca = pd.read_csv(ROOT / "data" / "h1b" / "lca_filings.csv")
    lca_curam_dc = lca[lca["query"].str.contains("curam", case=False, na=False) & (lca.dc_city == 1)]
    check("pipeline agrees: DC-worksite Curam filings", len(lca_curam_dc), 110)
    check("pipeline agrees: D2Sol's share",
          int(lca_curam_dc.employer.str.upper().str.startswith("D2SOL").sum()), 89)

    d2sol_careers = (ROOT / "evidence" / "job_ads" / "d2sol-careers.txt").read_text(encoding="utf-8")
    check("source: D2Sol hiring junior US roles", "Junior Curam Developer" in d2sol_careers, True)
    check("source: D2Sol hiring in Bangalore", "Bangalore - India" in d2sol_careers, True)

    section("the OCR-recovered awards")
    # 74 scanned award documents were recovered by analysis/ocr_awards.py and
    # classified into data/delivery_structure_ocr.csv (separate file; the
    # native results are untouched). The briefing states combined figures.
    ocr = pd.read_csv(ROOT / "data" / "delivery_structure_ocr.csv")
    check("awards recovered by OCR", len(ocr), 75)
    # CW120603 is in both files: its award was a scan when the OCR pass ran, and
    # the copy the pipeline now reads has a text layer. The combined figures
    # count contracts rather than rows, so the overlap is deduplicated -- the
    # native reading wins -- and this check is what says how big it is.
    overlap = set(ocr.contract_number) & set(delivery.contract_number)
    check("overlap with the native readable set", len(overlap), 1)
    combined = pd.concat([delivery, ocr], ignore_index=True).drop_duplicates("contract_number")
    ocr_only = len(set(ocr.contract_number) - set(delivery.contract_number))
    check("read from OCR text alone", ocr_only, 74)
    check("readable awards combined", len(combined), 387)
    check("candidates with no award document at all", candidates - len(combined), 93)
    # The per-structure counts this section used to assert backed the six-way
    # table on the site, which has been removed. See the note in "delivery
    # structure" above.
    combined_commits = combined[combined.agile_mention_kind == "delivery_commitment"]
    check("combined delivery commitments", len(combined_commits), 17)
    check("combined agile-as-hiring-requirement",
          int((combined.agile_mention_kind == "personnel_qualification").sum()), 18)

    section("purchase-order vendor figures")
    recent = orders[orders.fiscal_year.between(2022, 2026)].copy()
    recent["vendor_key"] = recent.vendor.str.upper().str.strip()
    cai = recent[recent.vendor_key == "COMPUTER AID  INC"]
    check("Computer Aid FY22-26 $M", round(cai.amount.sum() / 1e6), 466, 3)
    check("Computer Aid purchase orders", len(cai), 5814, 40)
    check("Computer Aid median PO $", round(cai.amount.median()), 59755, 200)
    check("Computer Aid distinct agencies", cai.agency.nunique(), 33, 1)
    fast = recent[recent.vendor_key == "FAST ENTERPRISES LLC"]
    check("FAST Enterprises $M", round(fast.amount.sum() / 1e6), 51, 2)
    check("FAST Enterprises purchase orders", len(fast), 11, 1)
    check("FAST median PO $M", round(fast.amount.median() / 1e6, 1), 3.5, 0.2)

    section("competition")
    # The portal has a marketType field and, for technology, it never records a
    # sole source award. The justification is in the D&F documents instead.
    # analysis/competition.py reads them; these figures come off its output, so
    # a re-run that reads more documents will move them upward. They are a
    # floor, not a rate: 652 of the 1,009 technology contracts carrying a D&F
    # have not been sampled at all.
    comp = pd.read_csv(ROOT / "data" / "competition" / "contract_competition.csv")
    market = {
        (d.get("marketType") or "(none)")
        for d in details
        if d["contractNumber"] in set(labels.loc[labels.is_technology.astype(bool), "contract_number"])
    }
    check("technology contracts recorded as sole source",
          sum(1 for m in market if vocabulary.is_sole_source_market_type(m)), 0)
    # Distinct contracts, not rows: a contract appears once per option period.
    # Five spellings are in use and matching one of them undercounts. They are
    # listed, with a contract named against each, in
    # procurement_vocabulary.yaml -- the same list analysis/competition.py
    # reads, so the two cannot drift apart into different answers.
    sole_corpus = {
        d["contractNumber"]
        for d in details
        if vocabulary.is_sole_source_market_type(d.get("marketType"))
    }
    check("contracts the portal does mark sole source", len(sole_corpus), 24)

    def _flag(name: str) -> pd.Series:
        return comp[name].fillna(False).astype(bool)

    limiting = comp[_flag("limits_competition") | _flag("exempt_from_competition")
                    | _flag("single_source_language")]
    check("technology contracts with competition-limiting paperwork", len(limiting), 75)
    check("their ceilings $M", round(limiting.ceiling.sum() / 1e6, 1), 63.3, 0.5)
    check("of those, the portal calls open",
          int((limiting.market_type == "Open").sum()), 44)
    check("contracts citing the software lock-in provision",
          int(_flag("software_lock_in_cite").sum()), 9)
    check("D&F documents read", int((comp.coverage == "read").sum()), 294)
    # Every sampled scan was recovered by OCR, so "still unreadable" is now zero
    # and the informative number is how many contracts the transcriptions carry.
    check("contracts recovered by OCR", int((comp.coverage == "ocr_readable").sum()), 63)
    check("contracts still unreadable", int((comp.coverage == "scans_only").sum()), 0)
    check("findings that rest on a transcription",
          int(comp["finding_rests_on_ocr"].fillna(False).astype(bool).sum()), 25)
    check("contracts not yet sampled", int((comp.coverage == "not_sampled").sum()), 652)

    section("the site's own figures")
    # site/index.html carries the same figures in prose and, until now, nothing
    # checked it. The README had this cover and the site did not, which is the
    # wrong way round: the site is the page a reader opens. Same construction as
    # the README block below -- write the figure the way the site writes it and
    # assert the string is still there.
    #
    # Only figures the site writes as numerals can be checked this way. It
    # spells out a number that opens a sentence ("Twenty-five rested on a
    # passage that describes a process"), and a check built from the data
    # cannot produce that spelling, so those sentences are not covered.
    site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    comp_open = int((limiting.market_type == "Open").sum())
    for phrase in [
        f"{len(tech):,} contracts classified as technology, "
        f"${tech.ceiling.sum() / 1e6:,.0f}M in ceilings",
        f"{len(lr):,} not-to-exceed rates in all",
        f"{len(limiting)}, carrying ${limiting.ceiling.sum() / 1e6:,.1f}M of ceiling",
        f"{comp_open} of those are labelled Open",
        f"{len(sole_corpus)} contracts across the whole corpus do",
        f"agrees with those {len(hand)} hand labels",
    ]:
        check(f"site: {phrase!r}", phrase in site, True)

    section("the README's own figures")
    # The README restates published figures in prose, and nothing used to check
    # it: it carried a stale rate count (2,146, the native-only subtotal) for
    # long enough to ship. Each entry below writes a computed figure the way the
    # README writes it and asserts that string is still in the file, so prose
    # drift fails here rather than going out.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    custom = labels[labels.work_type == "custom_software_build"]
    vehicles = labels[labels.work_type == "ordering_vehicle"]
    staffaug = labels[labels.work_type == "staff_augmentation"]
    for phrase in [
        f"Of {len(tech):,} technology contracts",
        f"${tech.ceiling.sum() / 1e9:.1f}B in ceilings",
        f"{len(custom)} contracts and ${round(custom.ceiling.sum() / 1e6)}M",
        f"${staffaug.ceiling.sum() / 1e9:.1f}B of staff",
        f"Ordering vehicles\ncarry ${vehicles.ceiling.sum() / 1e9:.1f}B",
        f"{lr.contract_number.nunique()} award documents publish hourly rate cards",
        f"{len(lr):,}\nnot-to-exceed rates",
        f"${lr.hourly_rate.min():.2f} to ${round(lr.hourly_rate.max()):,}",
        f"The {len(lr):,} published rates",
        f"Of {len(hand)} award documents\nread by hand, "
        f"{int((hand.evidence_basis == 'explicit_process').sum())} describe a delivery process",
        f"agrees with its {len(hand)} hand labels "
        f"{round(100 * (scored.delivery_structure == scored.model).mean())}% of the time",
        f"{len({c for c, _ in mod_names}):,} contracts\ncarry {len(mod_names):,} modifications",
        f"the {len(fixed)} build contracts whose award document fixes scope in",
        f"average {round(fixed.modifications.mean(), 1)} modifications against "
        f"{round(rest.modifications.mean(), 1)} for the other {len(rest)}",
        f"Of\n{len(actions)} modification documents read, {int((actions.outcome == 'read').sum())} "
        f"were legible, and {int(changed.sum())} of those change what",
        f"{len(contracts):,} rows\nare {contracts.contract_number.nunique():,} contracts",
        f"Of {len(payments):,} rows, {int(payments.contract_number.isna().sum()):,} carry",
        f"${payments.check_total.sum() / 1e9:.1f}B against "
        f"${payments.amount.sum() / 1e9:.1f}B of actual vouchers",
        f"{len(combined)} of {candidates} candidate contracts: "
        f"{len(delivery)} with\na native text layer, {ocr_only} recovered",
        f"The remaining {candidates - len(combined)} have no award document",
    ]:
        shown = phrase.replace("\n", " ")
        check(f"README: '{shown}'", phrase in readme, True)

    return list(RESULTS)


def write_json(results: list[Result], path: Path) -> None:
    """Write the results as a JSON array, in the order the checks ran."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.as_dict() for r in results], indent=1) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json",
        metavar="PATH",
        type=Path,
        help="also write one record per check to PATH, for drift reporting",
    )
    args = parser.parse_args(argv)

    results = run_checks()
    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else f'{len(FAILURES)} FAILED: ' + '; '.join(FAILURES)}")
    if args.json:
        write_json(results, args.json)
        print(f"wrote {args.json}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
