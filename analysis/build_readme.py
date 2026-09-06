#!/usr/bin/env python3
"""Render README.md from README.md.in, computing every figure it states.

Why this exists. The README used to carry its figures as hand-typed text, and
analysis/verify_claims.py checked a couple of dozen of them by writing a
computed figure the way the prose wrote it and asserting the string was still
in the file. That covered about a fifth of the numbers in the file, and the
part it did cover broke on things that have nothing to do with whether a figure
is right: rewrapping a sentence moved a number onto the next line and the
literal stopped matching, a figure the prose spelled out as a word could not be
produced from data at all, and twice the prose and the asserted literal
disagreed while the check still passed, because the check compared a computed
value against the file rather than against the sentence a reader sees.

Generating the file removes that class of problem. Every figure is computed
here, from the same tables verify_claims.py reads and with the same
expressions, and verify_claims.py now asserts one thing: that re-rendering
produces the committed README byte for byte. That covers every figure in the
file rather than a sample of them, and it cannot be fooled by wrapping or by a
number written as a word.

    uv run python analysis/build_readme.py            # write README.md
    uv run python analysis/build_readme.py --check    # fail if it would change

Placeholder syntax is ``{{name}}``. Braces were picked because the README
contains no brace character anywhere -- prose, tables or the shell code blocks
-- and Markdown gives them no meaning, so a placeholder cannot be produced by
accident. :func:`render` refuses to write a file with an unresolved ``{{`` left
in it and refuses a name it has no figure for, so a collision surfaces as a
build failure rather than as text on the page.

Traps:

* An identifier is not a figure. A statute number (2-354.13(8)), a contract
  number (CW95481), an NIGP class (918, 920, 961/962), a fiscal year and an
  eval case number stay literal in README.md.in. Templating one would let a
  rebuild renumber it.
* ``is_technology`` reads back out of the CSV as a numpy bool, which is not a
  Python bool and does not behave like one everywhere. It is coerced --
  ``.astype(bool)`` over the column, ``bool()`` over a single value -- before
  being used as a truth value.
* TRANSCRIBED holds figures the README states that nothing in this repository
  computes. They render from a literal, and every run says so on stderr, so
  they cannot be mistaken for something the data produced.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

TEMPLATE = ROOT / "README.md.in"
TARGET = ROOT / "README.md"

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

# Figures the README states that no script in this repository produces. They
# are written out here rather than left as literals in README.md.in so that
# every one of them is in a single list a reader can audit, and so a run says
# out loud how many there are. Nothing else in the rendered file is typed by
# hand.
#
#   top10_share_repaired_pct -- the top-ten payment share after repairing the
#   30-character truncation in the payments vendor field. The repaired figure
#   was computed once, by hand, and the repair was never written down; none of
#   the obvious reconstructions (truncate-then-alias, alias-then-truncate at
#   any width) reproduces 53.2 from the current snapshot, so guessing at one
#   would be inventing a computation to fit a published number.
TRANSCRIBED = {
    "top10_share_repaired_pct": "53.2",
}

_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
    13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
}


def words(value: int) -> str:
    """A small integer spelled out, for the places the README spells one.

    The README writes "appears three ways" and "exactly those two" rather than
    numerals, and a generated file has to reproduce the prose it replaced
    exactly. Without this the only options are leaving those figures hand-typed
    -- the thing this script exists to stop -- or rewriting the sentences to
    suit the renderer, which is a published change made for a tooling reason.

    Deliberately small. It raises above twenty rather than guessing, because a
    figure that grows past that wants a numeral and an edit to the sentence,
    not a word nobody chose.
    """
    if value not in _WORDS:
        raise ValueError(f"no spelling for {value}; write it as a numeral instead")
    return _WORDS[value]


def load_sources() -> dict[str, object]:
    """The tables every figure is computed from, loaded once.

    verify_claims.py already has these in hand when it calls :func:`render`,
    and passing them in is what keeps `make verify` from reading the 870,000
    row payments table twice.
    """
    return {
        "contracts": parse.load_contracts(),
        "payments": parse.load_payments(),
        "labels": pd.read_csv(ROOT / "data" / "contract_classifications.csv"),
        "delivery": pd.read_csv(ROOT / "data" / "delivery_structure.csv"),
    }


def compute(sources: dict[str, object] | None = None) -> dict[str, str]:
    """Every figure the README states, keyed by its placeholder name.

    Each expression here is the one analysis/verify_claims.py uses for the same
    figure, copied rather than rewritten: a figure computed two ways is a
    figure that will eventually disagree with itself.
    """
    src = sources or load_sources()
    contracts = src["contracts"]
    payments = src["payments"]
    labels = src["labels"]
    delivery = src["delivery"]

    figures: dict[str, str] = {}

    # -- the payments table -------------------------------------------------
    # "about 870,000 voucher lines": the README rounds, so the rounding is
    # part of the figure rather than something a reader is left to guess at.
    figures["payments_about"] = f"{round(len(payments), -4):,}"
    figures["payment_rows"] = f"{len(payments):,}"
    figures["payments_without_contract"] = f"{int(payments.contract_number.isna().sum()):,}"
    figures["payments_without_contract_b"] = (
        f"{payments[payments.contract_number.isna()].amount.sum() / 1e9:.1f}"
    )
    figures["payments_check_total_b"] = f"{payments.check_total.sum() / 1e9:.1f}"
    figures["payments_voucher_total_b"] = f"{payments.amount.sum() / 1e9:.1f}"
    figures["earliest_payment_fy"] = f"{int(payments.fiscal_year.min())}"
    figures["payments_before_window_b"] = (
        f"{payments[payments.fiscal_year < 2021].amount.sum() / 1e9:.1f}"
    )

    # STaR II is the contract the check-total field made look absurd. Its
    # ceiling is the sum of its option-period rows, which is what every other
    # ceiling in this project is.
    star_pay = payments[payments.contract_number == "CW95481"]
    figures["star_check_total_b"] = f"{star_pay.check_total.sum() / 1e9:.1f}"
    figures["star_ceiling_m"] = (
        f"{contracts[contracts.contract_number == 'CW95481'].amount.sum() / 1e6:.0f}"
    )

    # -- the contracts table ------------------------------------------------
    figures["contract_option_period_rows"] = f"{len(contracts):,}"
    figures["distinct_contracts"] = f"{contracts.contract_number.nunique():,}"
    starts = pd.to_datetime(contracts.start_date, errors="coerce")
    ends = pd.to_datetime(contracts.end_date, errors="coerce")
    figures["earliest_option_period_start"] = starts.min().strftime("%B %Y")
    figures["latest_option_period_end_year"] = f"{ends.max().year}"

    # -- the truncated vendor field -----------------------------------------
    # The truncation width is read off the data rather than asserted: the
    # portal pads no names, so the width shows up as the largest one-character
    # drop in the length distribution -- 73,061 names at 30 against 2,073 at
    # 31. The mode is no good here (17 is the commonest length), and neither is
    # the steepest ratio (a second, much smaller pile sits at 40).
    lengths = payments.vendor.astype(str).str.len().value_counts().sort_index()
    cliff = max(
        lengths.index,
        key=lambda n: int(lengths.get(n, 0)) - int(lengths.get(n + 1, 0)),
    )
    figures["vendor_truncation_chars"] = f"{int(cliff)}"
    figures["vendor_length_past_truncation"] = f"{int(cliff) + 1}"
    figures["vendor_names_at_truncation"] = f"{int(lengths.get(cliff, 0)):,}"
    figures["vendor_names_past_truncation"] = f"{int(lengths.get(cliff + 1, 0)):,}"

    # vendor_aliases.yaml is the record of which spellings the rules merge, and
    # verify_claims.py checks it still matches what they actually merge, so the
    # count of CDW Government's spellings is read from there rather than
    # recounted against a different rule.
    aliases = yaml.safe_load((ROOT / "vendor_aliases.yaml").read_text(encoding="utf-8"))
    figures["cdw_spellings_word"] = words(
        len((aliases.get("merged_by_rule") or {})["CDW GOVERNMENT"])
    )

    # -- technology payments, and why they do not make a time series --------
    tech = labels[labels.is_technology.astype(bool)]
    tech_numbers = set(tech.contract_number)

    window = payments[
        payments.fiscal_year.between(2021, 2025)
        & payments.contract_number.isin(tech_numbers)
    ].copy()
    window["vendor_key"] = window.vendor.map(parse.normalize_vendor)
    ranked = window.groupby("vendor_key").amount.sum().sort_values(ascending=False)
    figures["top10_share_pct"] = f"{100 * ranked.head(10).sum() / window.amount.sum():.1f}"

    # The artifact series: technology payments joined to the current-state
    # contracts table, plotted by fiscal year. Published as the thing not to
    # do, alongside the share of payment dollars that matches any classified
    # contract, which is what the series actually measures.
    by_fy = payments[payments.contract_number.isin(tech_numbers)].groupby("fiscal_year").amount.sum() / 1e6
    first_fy, last_fy = int(by_fy.index.min()), int(by_fy.index.max())
    figures["naive_join_first_fy_m"] = f"{by_fy.loc[first_fy]:.1f}"
    figures["naive_join_last_fy_m"] = f"{by_fy.loc[last_fy]:.0f}"
    figures["naive_join_fold"] = f"{by_fy.loc[last_fy] / by_fy.loc[first_fy]:.0f}"

    known = set(labels.contract_number)
    matched_pct = (
        100
        * payments[payments.contract_number.isin(known)].groupby("fiscal_year").amount.sum()
        / payments.groupby("fiscal_year").amount.sum()
    )
    figures["matched_share_first_fy_pct"] = f"{matched_pct.loc[first_fy]:.1f}"
    figures["matched_share_last_fy_pct"] = f"{matched_pct.loc[last_fy]:.1f}"

    # -- solicitations ------------------------------------------------------
    sols = json.loads((ROOT / "data" / "raw" / "solicitations.json").read_text(encoding="utf-8"))
    doc_ids = sum(1 for s in sols if str(s.get("solicitationNumber", "")).startswith("Doc"))
    figures["solicitation_doc_id_pct"] = f"{round(100 * doc_ids / len(sols))}"

    # -- purchase orders traced to ordering vehicles ------------------------
    # The order-to-contract link runs through the payments table, not the order
    # table: a payment line carries both numbers.
    booked = payments[payments.po_number.notna()].groupby("po_number")["contract_number"].nunique()
    figures["orders_resolved_to_contract"] = f"{int((booked > 0).sum()):,}"

    po_attr = pd.read_csv(ROOT / "data" / "vehicle_orders" / "po_contract_attribution.csv")
    activity = pd.read_csv(ROOT / "data" / "vehicle_orders" / "vehicle_order_activity.csv")
    traced = po_attr[po_attr.attribution == "A_traced_to_a_vehicle"]
    star = traced[traced.vehicle == "CW95481"]
    figures["traced_order_m"] = f"{traced.amount.sum() / 1e6:.1f}"
    figures["ordering_vehicles"] = f"{len(activity)}"
    figures["star_traced_m"] = f"{star.amount.sum() / 1e6:.1f}"
    figures["star_traced_share_pct"] = f"{100 * star.amount.sum() / traced.amount.sum():.1f}"
    figures["star_ordering_agencies"] = f"{star.agency.nunique()}"
    figures["vehicles_with_no_traced_order"] = f"{int((activity.traced_orders == 0).sum())}"
    silent = activity[activity.holder_orders_after_award == 0]
    figures["vehicles_holder_never_ordered"] = f"{len(silent)}"
    figures["silent_vehicle_ceiling_m"] = f"{silent.ceiling.sum() / 1e6:.1f}"

    # -- award documents read for delivery structure ------------------------
    build_types = {"custom_software_build", "cots_implementation",
                   "software_om_support", "it_consulting_advisory"}
    candidates = int(labels.work_type.isin(build_types).sum())
    ocr = pd.read_csv(ROOT / "data" / "delivery_structure_ocr.csv")
    combined = pd.concat([delivery, ocr], ignore_index=True).drop_duplicates("contract_number")
    figures["delivery_candidates"] = f"{candidates}"
    figures["readable_awards"] = f"{len(combined)}"
    figures["native_text_awards"] = f"{len(delivery)}"
    figures["ocr_recovered_awards"] = f"{len(set(ocr.contract_number) - set(delivery.contract_number))}"
    figures["awards_with_no_document"] = f"{candidates - len(combined)}"

    figures["published_rates"] = f"{len(pd.read_csv(ROOT / 'data' / 'labor_rates.csv')):,}"

    # -- why the contracts are classified by reading them --------------------
    # The miscoded contract the README names is one of the gold cases, so its
    # ceiling comes off the gold file rather than being looked up again.
    gold = pd.read_csv(ROOT / "tests" / "gold_labels.csv")
    plumbing = gold[gold.title.str.contains("Plumbing", case=False, na=False)]
    figures["plumbing_contract_ceiling_m"] = (
        f"{round(float(str(plumbing.amount.iloc[0]).strip('$').replace(',', '')) / 1e6)}"
    )

    # The classifier accuracies are measurements from a scored run, not
    # something recomputable without calling the model again, so they are read
    # from the file that records them. docs/CLASSIFIER_ACCURACY.md is the
    # published measurement and the README restates it.
    accuracy = _classifier_accuracy()
    figures["work_type_accuracy_pct"] = accuracy["work_type"]
    figures["is_technology_accuracy_pct"] = accuracy["is_technology"]

    hand = pd.read_csv(ROOT / "tests" / "hand_labels_delivery.csv")
    rater2 = pd.read_csv(ROOT / "tests" / "hand_labels_delivery_rater2.csv")
    pair = hand.merge(rater2, on="contract_number", suffixes=("", "_2"))
    scored = hand.assign(
        model=hand.contract_number.map(combined.set_index("contract_number").delivery_structure)
    )
    figures["delivery_hand_labels"] = f"{len(hand)}"
    figures["inter_rater_ceiling_pct"] = (
        f"{round(100 * (pair.delivery_structure == pair.delivery_structure_2).mean())}"
    )
    figures["delivery_agreement_pct"] = (
        f"{round(100 * (scored.delivery_structure == scored.model).mean())}"
    )

    # The agile eval scores the classifier against tests/delivery_gold_labels.csv
    # and expects exactly the misses recorded in delivery_overrides.yaml, so the
    # score is the case count less those, without running the model.
    agile_gold = pd.read_csv(ROOT / "tests" / "delivery_gold_labels.csv")
    overrides = yaml.safe_load((ROOT / "delivery_overrides.yaml").read_text(encoding="utf-8"))
    misses = len(overrides.get("agile_mention_kind") or {})
    figures["agile_eval_cases"] = f"{len(agile_gold)}"
    figures["agile_eval_score"] = f"{len(agile_gold) - misses}"
    figures["agile_eval_misses_word"] = words(misses)

    figures.update(TRANSCRIBED)
    return figures


def _classifier_accuracy() -> dict[str, str]:
    """The scored classifier accuracies, out of docs/CLASSIFIER_ACCURACY.md.

    The table there is the record of a scored run against blind hand labels.
    Recomputing it means calling the model, which a README build must not do,
    so the published measurement is read from the file that publishes it. A
    row that stops parsing raises here rather than rendering an empty figure.
    """
    text = (ROOT / "docs" / "CLASSIFIER_ACCURACY.md").read_text(encoding="utf-8")
    found = dict(re.findall(r"^\|\s*`(\w+)`\s*\|\s*\*\*(\d+)%\*\*", text, flags=re.MULTILINE))
    for name in ("work_type", "is_technology"):
        if name not in found:
            raise ValueError(f"no {name} row in docs/CLASSIFIER_ACCURACY.md")
    return found


def render(sources: dict[str, object] | None = None) -> str:
    """The README as it should be on disk, from the template and the figures.

    An unknown placeholder name and a leftover ``{{`` both raise. Silence is
    the failure mode worth ruling out: a name that fell through would render as
    empty text in the middle of a sentence and read like an editing slip.
    """
    template = TEMPLATE.read_text(encoding="utf-8")
    figures = compute(sources)

    missing = sorted({m.group(1) for m in PLACEHOLDER.finditer(template)} - set(figures))
    if missing:
        raise ValueError(f"README.md.in uses figures nothing computes: {', '.join(missing)}")

    out = PLACEHOLDER.sub(lambda m: figures[m.group(1)], template)
    if "{{" in out:
        raise ValueError("unresolved placeholder left in the rendered README")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the diff and exit non-zero instead of writing README.md",
    )
    args = parser.parse_args(argv)

    out = render()
    if TRANSCRIBED:
        print(
            f"{len(TRANSCRIBED)} figure(s) transcribed rather than computed: "
            + ", ".join(sorted(TRANSCRIBED)),
            file=sys.stderr,
        )

    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if args.check:
        if out == current:
            print("README.md is what README.md.in renders")
            return 0
        sys.stdout.writelines(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                out.splitlines(keepends=True),
                "README.md (committed)",
                "README.md (rendered)",
            )
        )
        return 1

    TARGET.write_text(out, encoding="utf-8")
    print(f"wrote {TARGET}" + ("" if out != current else " (unchanged)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
