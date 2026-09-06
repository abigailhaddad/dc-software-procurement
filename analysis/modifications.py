#!/usr/bin/env python3
"""What happens to a contract after it is awarded, read from its modifications.

This exists because a published claim went the wrong way. The briefing said
that contracts whose award document fixes the scope in advance cannot change
what is built without a contract modification, and offered that as evidence
that the work is locked down. The modification record does not support it: once
you condition on having run long enough to be modified at all, the fixed-scope
contracts are modified slightly *more* than the rest, and the modifications
themselves add work, delete line items and move deliverable dates. Fixing scope
in the award document does not mean the scope stays fixed.

Two counting traps and one comparison trap, all handled here rather than left
to the reader.

**The portal duplicates attachment rows, so document ids over-count.**
``contract_details.json`` lists 24,471 attachments in the "Modifications"
category across 1,609 contracts, and every one of those has a distinct
``id``. They are not 24,471 documents. A document id is base64, and it decodes
to the contract number, the option period and a GUID, separated by broken bars
-- so the same PDF filed under three option periods gets three ids. The portal
also repeats the same row inside a single record: CW77571's Option 1 record
lists 163 attachments, one of which is the same "Base Period - Mod 1" file
twenty times over, with twenty different GUIDs.

Four of those twenty were downloaded and hashed: 938,518 bytes,
sha256 91cbe3b6..., identical. Four attachments that share a filename across
different option periods were downloaded the same way, and each pair matched
too. So the filename, minus the ``CW#-{option period}-{category}-`` prefix the
portal prepends, is the document's identity, and deduplicating on it gives
**8,547** distinct modification documents rather than 24,471. Run
``--check-duplicates`` to redo that test; it is six downloads.

8,547 is the number to use. It is documents, not modifications: parsing a
modification number out of the free text (M0003, "Mod 4", "Modification No.
0012") succeeds on 8,223 of them and collapses to 7,882 distinct
(contract, modification number) pairs, because a handful of modifications are
filed twice under different names -- a draft and an executed copy, or a
supplemental page. The 324 that will not parse are real documents with
unparseable names ("Proposed Modification -CW100945.pdf", "Mod One for RAP"),
so the number-based count is a floor and the filename count is the honest one.
Both are written to the output.

**Raw modification counts measure duration, not volatility.** A contract with
one option-period row averages 1.3 modifications and one with three averages
5.3, which is most of the variance in the whole table. Any comparison between
two groups of contracts that does not hold duration fixed is comparing how long
they have been running. This script conditions two ways -- on having at least
two option-period rows and at least one modification, and separately on at
least 24 months of elapsed performance -- and reports both.

**The group split is the weakest part of the whole thing.** ``fixed scope``
here means the delivery classifier called the contract sequential and gated
*and* said its scope was fixed in advance, and that classifier agrees with the
80 blind hand labels 57% of the time (docs/CLASSIFIER_ACCURACY.md, and the
"delivery structure" section of verify_claims.py). Label noise pushes
any real difference between the groups toward zero, so a null here would mean
nothing. What it does establish is that the noise is not hiding a difference in
the direction the briefing claimed. And the conditioned comparison rests on
15 fixed-scope contracts, which is few enough that the size of the gap is not
worth arguing about. Treat the direction as a refutation of the old claim and
not as a finding of its own.

**Three modification PDFs in ten cannot be classified.** Of the 400 read for
this run, 19% are scans with no text layer, 3% have a text layer whose fonts
carry no character map so the extraction is mojibake, and a further 8% are
readable but have no block 14 that survives extraction -- a form variant, or
pdftotext reading the page in a column order that separates the heading from
its text. Those 30% are reported as unread, never as "no scope change". The
whole point of the exercise is that unread is not evidence of nothing having
happened.

Modifications that can be read are classified by regex, not by a model. DC uses
an SF30-style form whose numbered block 14, "Description of Amendment/
Modification", carries the whole substance of the change in formulaic language
("hereby exercises Option Year", "ADD CLIN", "Delete ... Replace", "the living
wage rate is hereby adjusted"). A model would add cost and a second source of
error to a problem that string matching solves. The scope and schedule patterns
are written for precision rather than recall, so the share they flag is a floor;
all 27 that they flagged in this run were read by hand and all 27 were real.
"Schedule" covers both a moved deliverable date and a rewritten Section F,
which is where the term and the deliverables schedule live.

    uv run python analysis/modifications.py
    uv run python analysis/modifications.py --limit 50    # try a few first
    uv run python analysis/modifications.py --no-download  # counts only
    uv run python analysis/modifications.py --check-duplicates
"""

from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.client import DCProcurementClient  # noqa: E402
from dcprocure.documents import MIN_TEXT_CHARS, document_text, readable_share  # noqa: E402

DETAILS = ROOT / "data" / "raw" / "contract_details.json"
DELIVERY = ROOT / "data" / "delivery_structure.csv"
DELIVERY_OCR = ROOT / "data" / "delivery_structure_ocr.csv"
CONTRACTS_OUT = ROOT / "data" / "modifications.csv"
ACTIONS_OUT = ROOT / "data" / "modification_actions.csv"

# The portal's attachment naming scheme: CW#-{option period}-{category}-{free
# text}. Splitting on "-" is safe for the first three fields because neither a
# contract number nor an option period nor a category contains one; the free
# text does, constantly, so it is rejoined.
MODIFICATION_CATEGORY = "Modifications"

# How many modification PDFs to fetch in one run. Each is around 2 MB on the
# wire and its text is cached afterwards, so a repeat run costs nothing.
DOWNLOAD_CAP = 400

# Documents are sampled, not taken in file order, and the seed is fixed so two
# runs on the same snapshot read the same documents.
SAMPLE_SEED = 20260906

# Enough elapsed performance that a modification has had a chance to happen.
MIN_MONTHS = 24

TODAY = pd.Timestamp("today").normalize()


def load_details() -> list[dict[str, Any]]:
    return json.loads(DETAILS.read_text(encoding="utf-8"))


def modification_attachments(details: list[dict[str, Any]]) -> pd.DataFrame:
    """Every attachment filed in the "Modifications" category, undeduplicated.

    One row per attachment row the portal returns, which is what over-counts.
    ``free_text`` is the filename with the portal's own prefix removed, and is
    the field to deduplicate on -- see the module docstring for why, and for
    the hashes.
    """
    rows = []
    for record in details:
        for document in record.get("documents") or []:
            name = document.get("name") or ""
            parts = name.split("-")
            if len(parts) <= 3 or parts[2] != MODIFICATION_CATEGORY:
                continue
            rows.append(
                {
                    "contract_number": record["contractNumber"],
                    "option_period": record.get("currentOptionPeriod"),
                    "free_text": "-".join(parts[3:]),
                    "document_id": document["id"],
                }
            )
    return pd.DataFrame(rows)


def deduplicate(attachments: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct modification document.

    Keeps the first id seen for each (contract, free text) pair. Which id is
    kept does not matter: the bytes behind them are identical.
    """
    return attachments.drop_duplicates(subset=["contract_number", "free_text"]).copy()


# Modification numbers in filenames: M0003, M 3, "Mod 4", "Modification No.
# 0012", "MOD-2". The separator class matters -- "Mod-0003" and "M0001_CW100150"
# both appear -- and the trailing (?!\d) rather than \b is deliberate, because a
# word boundary fails on "M0001_CW100150" where "_" is a word character and the
# number would be silently dropped.
_MOD_NUMBER = re.compile(
    r"(?:\bm|\bmod(?:ification)?s?)\.?[\s\-_#:]*(?:no\.?|number|#)?[\s\-_#:]*0*(\d{1,3})(?!\d)",
    re.I,
)


def parse_modification_number(free_text: str) -> int | None:
    """The modification number a filename claims, or None if it does not say."""
    match = _MOD_NUMBER.search(free_text)
    return int(match.group(1)) if match else None


def decode_document_id(document_id: str) -> str:
    """The portal's document id as the string it encodes.

    Only used to show that ids carry the option period, and therefore that two
    ids for one document are the normal case rather than a defect.
    """
    padded = document_id + "=" * (-len(document_id) % 4)
    return base64.b64decode(padded).decode("utf-8", "replace")


def opportunity(details: list[dict[str, Any]]) -> pd.DataFrame:
    """Per contract, how much chance it has had to be modified.

    Two measures, because they fail differently. ``option_periods`` is the
    number of records the portal holds for the contract, which is how far
    through its option years it has been taken; it is an integer and it is
    never missing. ``elapsed_months`` runs from the earliest start date to the
    earlier of the latest end date and today, which is closer to what actually
    matters but depends on dates the portal sometimes leaves blank. It is also
    measured against today's date, so it moves between runs on contracts that
    have not ended; nothing published rests on it, and the option-period count
    is what the conditioned comparison uses.
    """
    starts: dict[str, list[pd.Timestamp]] = collections.defaultdict(list)
    ends: dict[str, list[pd.Timestamp]] = collections.defaultdict(list)
    periods: collections.Counter[str] = collections.Counter()
    for record in details:
        number = record["contractNumber"]
        periods[number] += 1
        start = pd.to_datetime(record.get("startDate"), errors="coerce")
        end = pd.to_datetime(record.get("endDate"), errors="coerce")
        if pd.notna(start):
            starts[number].append(start)
        if pd.notna(end):
            ends[number].append(end)

    rows = []
    for number, count in periods.items():
        months = None
        if starts[number] and ends[number]:
            span = min(max(ends[number]), TODAY) - min(starts[number])
            months = max(0.0, span.days / 30.44)
        rows.append(
            {
                "contract_number": number,
                "option_periods": count,
                "elapsed_months": round(months, 1) if months is not None else None,
            }
        )
    return pd.DataFrame(rows)


def delivery_labels() -> pd.DataFrame:
    """The delivery-structure labels, native text and OCR-recovered together.

    The two files are kept apart everywhere else because the OCR pass was run
    later and the native results were deliberately left untouched. Here they
    are one population: whether a contract's award had a text layer says
    nothing about how it was modified afterwards.
    """
    frames = [pd.read_csv(DELIVERY)]
    if DELIVERY_OCR.exists():
        frames.append(pd.read_csv(DELIVERY_OCR))
    frame = pd.concat(frames, ignore_index=True)
    frame["fixed_scope"] = frame.delivery_structure.eq(
        "sequential_gated_deliverables"
    ) & frame.scope_fixed_in_advance.eq("yes")
    return frame[
        ["contract_number", "title", "work_type", "delivery_structure",
         "scope_fixed_in_advance", "fixed_scope"]
    ]


# -- reading one modification -------------------------------------------------

# Block 14 of DC's SF30-style amendment form. The "14." prefix is optional
# because pdftotext reads the form in column order on some layouts and strips
# the number off the heading; requiring it loses three documents in four
# hundred and gains nothing.
_BLOCK_14 = re.compile(
    r"(?:14\.\s*)?Description of [Aa]mendment\s*/?\s*[Mm]odification[^\n]*", re.I
)

# Where block 14 stops. The closing formula is the reliable one; the rest guard
# against running off the end of page one into the next page's header or into a
# "CONTRACT RECAP" table, which lists what *every previous* modification did and
# will otherwise contribute other modifications' verbs to this one.
_BLOCK_14_END = re.compile(
    r"\n\s*15[A-C]?\.\s"
    r"|Except as provided herein"
    r"|ALL OTHER TERMS AND CONDITIONS REMAIN"
    r"|All [Oo]ther [Tt]erms and [Cc]onditions remain"
    r"|CONTRACT RECAP"
    r"|AMENDMENT OF SOLICITATION",
    re.I,
)

_UCF_PREAMBLE = re.compile(r"^\s*\(?\s*Organized by UCF[^)\n]*\)?", re.I)

BLOCK_LIMIT = 3000


def description_block(text: str) -> str:
    """Block 14 of the amendment form, or "" when it cannot be found.

    Returns the substance of the modification with the form's own preamble
    ("Organized by UCF Section headings ...") removed and whitespace collapsed,
    which makes the patterns below tractable: pdftotext breaks these blocks
    across lines wherever the PDF's text runs happen to end.
    """
    opening = _BLOCK_14.search(text)
    if opening is None:
        return ""
    closing = _BLOCK_14_END.search(text, opening.end())
    stop = closing.start() if closing else opening.end() + BLOCK_LIMIT
    body = text[opening.end() : min(stop, opening.end() + BLOCK_LIMIT)]
    return " ".join(_UCF_PREAMBLE.sub("", body).split())


#: Language that says what is bought or built has changed. Written for
#: precision: every pattern names a mechanism (a change request, a new contract
#: line item, a rewritten Section C) rather than a mood. "Additional funding" is
#: not here, because more money for the same work is not a scope change.
SCOPE_CHANGE = [
    r"\bchange request\b|\bCR-\d",
    r"\badd(?:s|ed|ing)?\b[^.;]{0,40}\bCLIN\b",
    r"\bnew CLIN\b",
    r"\badditional\s+(?:work|services|tasks?|development|deliverables?"
    r"|functionality|requirements?|scope)\b",
    r"\bscope (?:of work )?is (?:hereby )?(?:revised|modified|amended|expanded"
    r"|increased|changed)\b",
    r"\b(?:statement|scope) of work\b[^.;]{0,60}\b(?:revised|replaced|deleted"
    r"|amended|modified)\b",
    r"\b(?:expand|increase)\w*\s+(?:the\s+)?scope\b",
    r"\bextended scope\b",
    r"\bSection\s+C[\.\d]*\b[^.;]{0,60}\b(?:delete|replace|revis|add|amend)",
    r"\b(?:delete|replace|revis|add|amend)\w*\b[^.;]{0,40}\bSection\s+C[\.\d]*\b",
]

#: Language that moves a date something is owed on. Deliberately separate from
#: extending the contract's term: a contract can run longer with the same
#: deliverables due on the same days, and that is a different event.
SCHEDULE_CHANGE = [
    r"\bdeliver(?:y|able)\w*\s+(?:schedule|dates?)\b[^.;]{0,50}"
    r"\b(?:revis|chang|extend|modif|delet|replac)",
    # F.1 is "Term of Contract" and F.2 is "Option to Extend" on DC's standard
    # form; deliverables start at F.3. Without excluding the first two, every
    # term extension and option exercise reads as a deliverable slipping:
    # CW96709 ("exercise option year one"), CW93311 twice, and CW102877 were all
    # caught this way, which is four of the six this flag used to fire on.
    r"\bSection\s+F(?!\.[12]\b)[\.\d]*\b[^.;]{0,60}\b(?:delete|replace|revis|amend)",
    r"\bmilestone\s+(?:dates?|schedule)\b[^.;]{0,50}\b(?:revis|chang|extend|modif)",
    r"\bchange (?:the )?(?:Period of Performance|implementation) dates?\b",
    r"\bdue date\b[^.;]{0,60}\b(?:delay|extend|revis|chang)",
]

#: The routine machinery of a contract running its course. These are not
#: mutually exclusive and are not meant to be: one modification commonly
#: exercises an option, moves money and changes the contracting officer.
#:
#: Exercising an option and extending the term are one category rather than two
#: because in this corpus they are one event. DC exercises an option by citing
#: the contract's own "Option to Extend the Term of the Contract" clause, so
#: separate patterns for the two flagged the same documents: 115 of the 123 that
#: matched an extension pattern also matched an option exercise, and most of the
#: remaining 8 were option exercises phrased around the clause title.
#:
#: The dollar alternation in ``funding_change`` is outside the ``\b`` group on
#: purpose. ``\b\$`` cannot match: "$" is not a word character, and neither is
#: the space in front of it, so there is no boundary between them, and an
#: earlier version of this pattern silently found 5 funding changes instead
#: of 27.
ROUTINE_ACTIONS: dict[str, str] = {
    "option_or_extension": r"\bexercis\w*\b[^.;]{0,40}\boption\b"
                           r"|\boption\s+(?:year|period)\s+\w+\s+is\s+hereby\s+exercis"
                           r"|\b(?:period of performance|term of the (?:contract"
                           r"|task order)|contract expiration date)\s+is\s+"
                           r"(?:hereby\s+)?extended"
                           r"|\bno[\s-]cost extension\b",
    "wage_adjustment": r"living wage|wage determination|minimum wage",
    "administrative": r"contracting officer|contract administrator|\bCOTR\b|\bCOR\b"
                      r"|administrative modification|requisition|name change|novation"
                      r"|change of address",
    "funding_change": r"\b(?:increase|decrease|add|de-?obligat)\w*\b[^.;]{0,60}"
                      r"(?:\b(?:not[\s-]to[\s-]exceed|NTE|funding|funds"
                      r"|total (?:contract )?(?:amount|price|value)|ceiling)\b"
                      r"|\$[\d,]{4,})",
}


def classify_block(block: str) -> dict[str, bool]:
    """What one modification's block 14 says it does.

    Every flag is independent. Nothing here is a residual category: a block
    that trips no pattern is recorded as tripping no pattern, which is a
    statement about these regexes and not about the modification.
    """
    flags = {
        name: bool(re.search(pattern, block, re.I))
        for name, pattern in ROUTINE_ACTIONS.items()
    }
    flags["scope_change"] = any(re.search(p, block, re.I) for p in SCOPE_CHANGE)
    flags["schedule_change"] = any(re.search(p, block, re.I) for p in SCHEDULE_CHANGE)
    return flags


# -- the confounded comparison ------------------------------------------------


def conditioned_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    """Modifications per contract, holding opportunity roughly fixed.

    Two conditionings, both reported, because neither is obviously right. The
    first keeps contracts that reached a second option period and were modified
    at least once, which is the crude version of "ran long enough to be
    modified". The second keeps contracts with at least two years of elapsed
    performance and divides by that, which is a rate rather than a count.
    """
    rows = []
    by_periods = frame[(frame.option_periods >= 2) & (frame.modifications > 0)]
    for fixed, group in by_periods.groupby("fixed_scope"):
        rows.append(
            {
                "conditioning": "2+ option periods, modified at least once",
                "group": "fixed scope" if fixed else "everything else",
                "contracts": len(group),
                "mean_modifications": round(group.modifications.mean(), 2),
                "median_modifications": float(group.modifications.median()),
            }
        )
    long_running = frame[frame.elapsed_months >= MIN_MONTHS].copy()
    long_running["per_year"] = long_running.modifications / (
        long_running.elapsed_months / 12
    )
    for fixed, group in long_running.groupby("fixed_scope"):
        rows.append(
            {
                "conditioning": f"{MIN_MONTHS}+ months elapsed, per elapsed year",
                "group": "fixed scope" if fixed else "everything else",
                "contracts": len(group),
                "mean_modifications": round(group.per_year.mean(), 2),
                "median_modifications": round(float(group.per_year.median()), 2),
            }
        )
    return pd.DataFrame(rows)


# -- downloading --------------------------------------------------------------


def sample_documents(
    frame: pd.DataFrame, modifications: pd.DataFrame, limit: int
) -> pd.DataFrame:
    """Which modification documents to read, inside the download cap.

    Every modification on a fixed-scope contract is taken -- that group is the
    one the claim was about, and it is small enough to cover completely -- and
    the remaining budget goes to a seeded random sample of the rest. The
    denominators are known on both sides, so the shares are comparable even
    though the sampling rates are not equal.
    """
    conditioned = frame[(frame.option_periods >= 2) & (frame.modifications > 0)]
    pool = modifications[modifications.contract_number.isin(conditioned.contract_number)]
    fixed_numbers = set(conditioned[conditioned.fixed_scope].contract_number)
    pool = pool.assign(fixed_scope=pool.contract_number.isin(fixed_numbers))

    fixed = pool[pool.fixed_scope]
    other = pool[~pool.fixed_scope].sample(frac=1.0, random_state=SAMPLE_SEED)
    return pd.concat([fixed, other.head(max(0, limit - len(fixed)))], ignore_index=True)


def read_documents(targets: pd.DataFrame, workers: int = 8) -> list[dict[str, Any]]:
    """Fetch, cache and classify each sampled modification.

    Three outcomes, kept apart: readable with a block 14, readable without one
    (a form variant, or pdftotext reading the columns in an order that separates
    the heading from its text), and not readable at all. Only the first is
    classified; the other two are carried into the output with their reason so
    that nothing unread is counted as nothing happening.
    """
    rows: list[dict[str, Any]] = []

    def read(target: Any) -> dict[str, Any]:
        record = {
            "contract_number": target.contract_number,
            "fixed_scope": bool(target.fixed_scope),
            "free_text": target.free_text,
            "modification_number": parse_modification_number(target.free_text),
            "document_id": target.document_id,
        }
        try:
            text = document_text(client, target.document_id)
        except Exception as exc:  # a missing or refused document is data, not a crash
            return {**record, "outcome": f"download failed: {exc}", "chars": 0}
        record["chars"] = len(text)
        if len(text) < MIN_TEXT_CHARS:
            return {**record, "outcome": "scanned, no text layer"}
        if readable_share(text) < 0.85:
            return {**record, "outcome": "text layer is mojibake"}
        block = description_block(text)
        if not block:
            return {**record, "outcome": "no block 14 found"}
        return {
            **record,
            "outcome": "read",
            "block": block[:600],
            **classify_block(block),
        }

    with DCProcurementClient() as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(read, targets.itertuples()))
    return rows


def check_duplicate_bytes(details: list[dict[str, Any]]) -> None:
    """Download six attachments and show that the duplicated ids are one file.

    Three pairs: the first two share a filename inside a single option-period
    record, the rest share one across option periods. This is the evidence for
    the deduplication decision in the docstring, and it is six requests, so it
    is behind a flag rather than in the default run.
    """
    attachments = modification_attachments(details)
    grouped = attachments.groupby(["contract_number", "free_text"])
    pairs = [
        (key, group)
        for key, group in grouped
        if len(group) >= 2
    ]
    picked = random.Random(SAMPLE_SEED).sample(pairs, 3)
    with DCProcurementClient() as client:
        for (number, free_text), group in picked:
            digests = []
            for row in group.head(2).itertuples():
                content = client.attachment(row.document_id)
                digests.append((len(content), hashlib.sha256(content).hexdigest()[:12]))
            same = "identical" if len(set(digests)) == 1 else "DIFFERENT"
            print(f"  {number}  {free_text[:52]}")
            for (size, digest), row in zip(digests, group.head(2).itertuples()):
                print(f"    {size:>9,} bytes  sha256 {digest}  "
                      f"{decode_document_id(row.document_id)[-38:]}")
            print(f"    -> {same}")


# -- report -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DOWNLOAD_CAP,
                        help=f"documents to download (default {DOWNLOAD_CAP})")
    parser.add_argument("--no-download", action="store_true",
                        help="counts and the conditioned comparison only")
    parser.add_argument("--check-duplicates", action="store_true",
                        help="download six attachments and hash them")
    args = parser.parse_args()

    details = load_details()
    attachments = modification_attachments(details)
    documents = deduplicate(attachments)

    print("-- counting modifications --")
    print(f"  {len(attachments):,} attachment rows in the "
          f"'{MODIFICATION_CATEGORY}' category")
    print(f"  {attachments.document_id.nunique():,} distinct document ids")
    print(f"  {len(documents):,} distinct documents after deduplicating on filename")
    numbers = documents.free_text.map(parse_modification_number)
    parsed = numbers.notna()
    pairs = documents[parsed].assign(n=numbers[parsed]).drop_duplicates(
        subset=["contract_number", "n"]
    )
    print(f"  {int(parsed.sum()):,} of those carry a parseable modification number, "
          f"giving {len(pairs):,} distinct (contract, modification number) pairs")
    print(f"  {int((~parsed).sum()):,} do not parse, so that is a floor; "
          "the filename count is the one used below")
    print(f"  spread over {documents.contract_number.nunique():,} contracts")

    if args.check_duplicates:
        print("\n-- are two ids for one filename two documents? --")
        check_duplicate_bytes(details)

    counts = documents.groupby("contract_number").size().rename("modifications")
    raw_counts = attachments.groupby("contract_number").size().rename("attachment_rows")
    frame = delivery_labels().merge(opportunity(details), on="contract_number", how="left")
    frame = frame.join(counts, on="contract_number").join(raw_counts, on="contract_number")
    frame[["modifications", "attachment_rows"]] = frame[
        ["modifications", "attachment_rows"]
    ].fillna(0).astype(int)

    print("\n-- modifications are mostly a function of how long a contract ran --")
    by_periods = frame.groupby("option_periods").modifications.agg(["size", "mean"])
    for periods, row in by_periods.iterrows():
        print(f"  {periods} option-period row(s): {int(row['size']):>4} contracts, "
              f"{row['mean']:.2f} modifications on average")
    print("  so no comparison between groups means anything without holding this fixed")

    print("\n-- fixed scope against everything else, conditioned --")
    comparison = conditioned_comparison(frame)
    for row in comparison.itertuples():
        print(f"  [{row.conditioning}] {row.group}: n={row.contracts}, "
              f"mean {row.mean_modifications}, median {row.median_modifications}")
    fixed_n = int(comparison[
        (comparison.group == "fixed scope")
        & comparison.conditioning.str.startswith("2+")
    ].contracts.iloc[0])
    print(f"  the fixed-scope side of the first row is {fixed_n} contracts. That is "
          "thin, and\n  the label splitting the groups agrees with blind hand labels "
          "57% of the time,\n  so read the direction and not the size.")

    frame.to_csv(CONTRACTS_OUT, index=False)
    print(f"\nwrote {CONTRACTS_OUT} ({len(frame):,} rows)")

    if args.no_download:
        return 0

    targets = sample_documents(frame, documents, args.limit)
    eligible = documents[documents.contract_number.isin(
        frame[(frame.option_periods >= 2) & (frame.modifications > 0)].contract_number
    )]
    print(f"\n-- reading {len(targets):,} of {len(eligible):,} modification documents "
          "on the conditioned contracts --")
    rows = read_documents(targets)
    actions = pd.DataFrame(rows)

    outcomes = actions.outcome.value_counts()
    for outcome, count in outcomes.items():
        print(f"  {count:>4} {outcome} ({count / len(actions):.0%})")
    unread = int((actions.outcome != "read").sum())
    print(f"  {unread} of {len(actions)} ({unread / len(actions):.0%}) could not be "
          "classified. They are\n  reported as unread, not as unchanged.")

    read = actions[actions.outcome == "read"]
    print("\n-- what the readable modifications do --")
    for name in list(ROUTINE_ACTIONS) + ["scope_change", "schedule_change"]:
        share = read[name].mean()
        print(f"  {int(read[name].sum()):>4} {name} ({share:.0%})")
    either = read.scope_change | read.schedule_change
    print(f"  {int(either.sum())} of {len(read)} ({either.mean():.0%}) change scope or "
          "a deliverable date")
    for fixed, group in read.groupby("fixed_scope"):
        touched = group.scope_change | group.schedule_change
        label = "fixed scope" if fixed else "everything else"
        print(f"    {label}: {int(touched.sum())} of {len(group)} ({touched.mean():.0%}) "
              f"on {group.contract_number.nunique()} contracts")

    actions.to_csv(ACTIONS_OUT, index=False)
    print(f"\nwrote {ACTIONS_OUT} ({len(actions):,} rows)")
    print(f"full coverage of the conditioned contracts would need {len(eligible):,} "
          f"downloads; the whole\nportal's modification record is {len(documents):,} "
          "documents, roughly 2 MB each.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
