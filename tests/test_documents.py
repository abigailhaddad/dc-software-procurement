#!/usr/bin/env python3
"""Check the two document-defect detectors in dcprocure.documents.

Both exist because a document read went wrong silently and nothing in the data
said so. CW106343 was classified, and hand-labelled, off CW107160's award PDF;
CW123311's award has a text layer whose fonts carry no character map, so it
extracted as 77,000 characters of mojibake and passed the length check on its
way to the classifier.

The fixtures are literal slices of those two documents rather than invented
strings, because what has to be caught is the specific shape of these failures:
glyph codes decoded as Latin-1 and control bytes in one case, a correct-looking
contract in the other whose page footer carries somebody else's number.

Assertions here are on the functions, not on corpus counts: data/raw is
refreshed weekly and is not in the repo, so a pinned count would fail for the
wrong reason. The corpus is scanned at the end and the counts printed, when a
cache happens to be present.

    uv run python tests/test_documents.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.documents import (  # noqa: E402
    MIN_READABLE_SHARE,
    MIN_TEXT_CHARS,
    _text_path,
    delivery_excerpt,
    looks_unreadable,
    other_contracts_named,
    pick_award_document,
    readable_share,
)

# Verbatim from CW123311's award document, the failure MIN_TEXT_CHARS misses:
# a text layer that yields glyph codes instead of characters.
MOJIBAKE = (
    "7]ÿ297Cÿ\x106\x13=\x122\x12\x13\x142Áÿ6/\x106/2/\x14\x11\x0f\x11\x12\x13\x142Áÿ "
    "Þ\x13=/6\x14:/\x14\x11â2ÿ2\x13\\\x127\x12\x11\x0f\x11\x12\x13\x14ÿ\x0f\x14VÿX\x1396ÿ"
    "\x1311/6Áÿ\x0f\x14Vÿ[;]ÿ\x11C\x122ÿ7/6\x11\x121\x127\x0f\x11\x12\x13\x142Áÿ\x0f\x14Vÿ"
    "2\x10/7\x121\x127\x0f\x11\x12\x13\x142Áÿ\x0f2ÿ\x0f6/ÿ\x0f\x11\x11\x0f7C/Vÿ\x136ÿ"
    "\x12\x147\x136\x10\x136\x0f\x11/Vÿ;Xÿ6/1/6/\x147/ÿ\x0f\x97\x0f6"
)

# The same boilerplate, extracted from a document whose fonts are intact.
READABLE = (
    "The following documents are incorporated by reference into this "
    "solicitation and your offer, and (b) this contract: certifications, "
    "representations, and specifications, as are attached or incorporated by "
    "reference. Contractor agrees to furnish and deliver all items."
)

# CW106343's award, abbreviated. Its own footer names CW107160 throughout.
WRONG_DOCUMENT = (
    "AWARD/CONTRACT 2. Contract Number CBE Set-Aside 3. Effective Date "
    "CW107160 4. Solicitation/Requisition/Purchase Request/Project No. "
    "MOBIS202313 See Block 20C. "
    "DC Supply Schedule Contract No.: CW107160 Page 3 of 26 "
    "1.5 ORDERING LIMITATIONS AND INFORMATION"
)

# A task order that cites the vehicle it was ordered against as well as itself.
# Routine, correct, and it must not be flagged.
CITES_PARENT = (
    "TASK ORDER/DELIVERY ORDER FOR SERVICES 2. TASK ORDER AGREEMENT NO. "
    "CW118123 4. CONTRACT NUMBER DCSS - CW95048 See Section 30C"
)

# Most awards name no CW number at all: the CW number is the portal's, and the
# paper carries the solicitation number instead.
NAMES_NO_NUMBER = (
    "AWARD/CONTRACT Contract No. DCKA-2024-C-0007 FULLY EXECUTED "
    "GOVERNMENT OF THE DISTRICT OF COLUMBIA"
)

FAILURES: list[str] = []


def check(name: str, got: object, want: object) -> None:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {got!r}")
    if not ok:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def test_readability() -> None:
    print("readability")
    check("mojibake is unreadable", looks_unreadable(MOJIBAKE), True)
    check("clean prose is readable", looks_unreadable(READABLE), False)
    check("mojibake share below threshold",
          readable_share(MOJIBAKE) < MIN_READABLE_SHARE, True)
    check("clean prose share above threshold",
          readable_share(READABLE) > MIN_READABLE_SHARE, True)
    # The length check the mojibake slips past, which is the whole point.
    check("mojibake would pass a length-only check",
          len(MOJIBAKE * 200) >= MIN_TEXT_CHARS, True)
    check("empty text scores zero", readable_share(""), 0.0)
    check("whitespace-only text scores zero", readable_share("   \n\t "), 0.0)


def test_contract_number() -> None:
    print("contract number")
    check("wrong document names another contract",
          other_contracts_named(WRONG_DOCUMENT, "CW106343"), ["CW107160"])
    check("the right document names nothing else",
          other_contracts_named(WRONG_DOCUMENT, "CW107160"), [])
    check("citing the parent vehicle is not a defect",
          other_contracts_named(CITES_PARENT, "CW118123"), [])
    check("naming no CW number is not a defect",
          other_contracts_named(NAMES_NO_NUMBER, "CW115205"), [])
    # Fixed digit width: CW884 is a fragment, not a five-digit contract number,
    # and an unbounded \d+ matched it against CW88474's own award.
    check("digit-count fragments are not contract numbers",
          other_contracts_named("prior award CW884 refers", "CW88474"), [])
    # Non-CW numbering schemes are compared only against their own scheme.
    check("DCRL numbers compare within their scheme",
          other_contracts_named("Contract No. DCRL-2022-C-0111", "DCRL-2021-C-0048"),
          ["DCRL-2022-C-0111"])
    check("a CW number says nothing about a DCRL contract",
          other_contracts_named("DC Supply Schedule CW95048", "DCRL-2021-C-0048"), [])


def scan_corpus() -> None:
    """Report what the two checks flag across whatever text cache is present."""
    details_path = ROOT / "data" / "raw" / "contract_details.json"
    if not details_path.exists():
        print("corpus scan skipped: no data/raw snapshot (make pull)")
        return

    details = json.loads(details_path.read_text(encoding="utf-8"))
    read = misfiled = unreadable = 0
    for detail in details:
        document = pick_award_document(detail)
        if document is None:
            continue
        cached = _text_path(document["id"])
        if not cached.exists():
            continue
        text = gzip.decompress(cached.read_bytes()).decode("utf-8")
        if len(text) < MIN_TEXT_CHARS:
            continue
        excerpt = delivery_excerpt(text)
        if not excerpt:
            continue
        read += 1
        # Readability is measured on the excerpt, not the document: the broken
        # font is usually confined to the cover form. See looks_unreadable.
        if looks_unreadable(excerpt):
            unreadable += 1
        if other_contracts_named(text, detail["contractNumber"]):
            misfiled += 1

    print(f"corpus scan: {read:,} cached award documents with a usable excerpt")
    print(f"  {unreadable:,} excerpts are mojibake")
    print(f"  {misfiled:,} documents name a contract other than their own")


def main() -> int:
    test_readability()
    test_contract_number()
    scan_corpus()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s):")
        for failure in FAILURES:
            print(f"  {failure}")
        return 1
    print("\nNo errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
