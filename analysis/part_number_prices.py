#!/usr/bin/env python3
"""Does DC pay different prices for the same part number?

THE QUESTION. If the reseller layer costs something, the cheapest place to see
it is a part number bought twice: same manufacturer's item, two contracts, two
prices. Award documents do publish part numbers next to unit prices --
CW62413's modification deletes "CLIN:1002 / Education Service Unit - 100 Units
PART No: EDU-UNIT-100, Qty 1, Unit Price: $45,125.00" -- so the raw material
exists.

THE ANSWER IS THAT THEY DO NOT REPEAT. Across all 1,380 cached award and
modification texts (558 contracts, 542 of them technology), 30,240 distinct
part-number-shaped tokens appear and 29,854 of them appear in exactly one
contract. Of the 386 that appear in more than one, nearly all are procurement
furniture rather than products: D.C. Code citations (``2-354.13``), GSA schedule
numbers (``GS-35F-0119Y``), Special Item Numbers (``132-51``), modification
numbers (``M0003``), form numbers (``SF-1444``), phone numbers, and -- the trap
worth naming -- ``BMC91`` and ``BMC91X``, which look like Broadcom part numbers
and are the FMCSA insurance filings named in nineteen contracts' standard
insurance clause. :func:`shared_tokens` produces that list; the reader
adjudicates it, because no rule separates a part number from a statute cite.

WHAT SURVIVES ADJUDICATION is :data:`REPEATED_PRODUCTS`: six product families
that genuinely appear in more than one award. Five of them are the same
reseller renewing the same subscription in a later year -- Sun Management
renewing support on Palo Alto appliances it can identify by serial number,
Document Systems renewing the same IBM FileNet licences, Pittman Vantix
renewing the same Genetec platform, Carahsoft renewing Salesforce -- which
measures escalation, not competition. One crosses resellers, and it is
reported in full below.

SO THE PRICE-SPREAD QUESTION CANNOT BE ANSWERED FROM AWARDS. One matched pair
is not a spread. This script's output is the count, the adjudicated list, and
the single comparison, so the negative result is checkable rather than
asserted.

TWO THINGS FOUND ALONG THE WAY, both recorded because they are where a
follow-up would start.

* The same part number *does* repeat inside one contract at different prices.
  CW92388 prices IBM part E0501LL at $324.77 for 162 units at one site and
  $407.06 for 40 units at another, in the same award, from the same vendor, on
  the same day: a 25% spread that is entirely quantity. Any cross-contract
  spread would have to clear that bar before it meant anything.
* The place where one part number really is priced by several vendors is the
  DC Supply Schedule catalogs, which schedule holders file as award
  attachments. Two are cached. They overlap on seven part numbers -- only one
  page of the larger catalog is in the text cache -- and the prices differ, but
  those are ceiling prices adopted from each holder's GSA schedule rather than
  prices DC paid, they are from different fiscal years, and the second
  catalog's column order cannot be settled from the dump. See
  :func:`dcss_catalog_overlap` and the caveats printed with it. This is a lead,
  not a finding.

Everything is regex and counting over the cached text in ``data/pdftext`` and
``data/pdftext_ocr``. Nothing is fetched and no model is called.

    uv run python analysis/part_number_prices.py
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.documents import _digest

DETAILS_PATH = ROOT / "data" / "raw" / "contract_details.json"
TEXT_DIRS = (ROOT / "data" / "pdftext", ROOT / "data" / "pdftext_ocr")
OUT = ROOT / "data" / "part_numbers"

#: A token shaped like a part number: starts alphanumeric, contains a digit and
#: a letter, five to thirty characters, hyphens and dots allowed inside. Loose
#: on purpose -- part numbers have no common shape -- so the output has to be
#: adjudicated rather than trusted.
TOKEN = re.compile(
    r"(?<![A-Za-z0-9\-\./])"
    r"((?=[A-Z0-9\-\./]*\d)(?=[A-Z0-9\-\./]*[A-Z])[A-Z0-9][A-Z0-9\-\./]{4,29})"
    r"(?![A-Za-z0-9\-\./])"
)

#: Section and paragraph references (``B.3.1``, ``2.1.4``), which the token
#: pattern otherwise floods the output with: 178 contracts share ``G.1.1``.
SECTION = re.compile(r"^[A-Z]?\.?\d{1,3}(\.\d+)*\.?$")

#: Prefixes that are never a part number in this corpus: DC contract and
#: capital-project numbers, solicitation types, GSA schedule and BPA numbers.
NOT_PART_PREFIXES = ("CW", "DCAM", "RFP", "RFQ", "GS-", "47Q", "DCSS")

#: Vocabulary a document uses when it is listing part numbers at all.
PART_VOCABULARY = re.compile(
    r"part\s*(?:no|number|#)|mfr\s*part|manufacturer\s*part|\bsku\b", re.IGNORECASE
)

#: Cross-contract matches read by hand, with what they are. Every token that
#: appears in more than one contract was reviewed; these are the ones that are
#: a manufacturer's part number for a product rather than procurement
#: furniture. ``cross_reseller`` marks the only family where two different
#: companies sold DC the same item.
REPEATED_PRODUCTS: dict[str, dict] = {
    "KMSATD-N-J12-G": {
        "product": "KnowBe4 Security Awareness Training Subscription, Diamond tier",
        "contracts": ["CW100632", "CW118489"],
        "cross_reseller": True,
        "note": "MVS then Avid Systems, both OCTO, both 36,000 seats",
    },
    "CLR-MPD-USGOV-1Y": {
        "product": "Genetec Clearance digital evidence platform, MPD, USGOV",
        "contracts": ["CW88137", "CW126928"],
        "cross_reseller": False,
        "note": "Pittman Vantix both times; a renewal four years apart",
    },
    "PAN-PA-5250-TP-HA2-R": {
        "product": "Palo Alto threat prevention subscription, PA-5250 HA pair",
        "contracts": ["CW66765", "CW105049"],
        "cross_reseller": False,
        "note": "Sun Management both times; the same appliance serial numbers",
    },
    "PAN-SVC-PREM-5250-R": {
        "product": "Palo Alto premium support renewal, PA-5250",
        "contracts": ["CW66765", "CW105049"],
        "cross_reseller": False,
        "note": "Sun Management both times",
    },
    "E0501LL": {
        "product": "IBM FileNet Content Manager Authorized User Value Unit",
        "contracts": ["CW92388", "CW131291", "CW137541"],
        "cross_reseller": False,
        "note": "Document Systems all three times",
    },
    "E0E6ALL": {
        "product": "Curam Business Intelligence and Analytics Authorized User",
        "contracts": ["CW85955", "CW111811"],
        "cross_reseller": False,
        "note": "IBM, then Merative after IBM sold Curam to it; maker direct both times",
    },
    "121-0175": {
        "product": "Salesforce Government Cloud Plus",
        "contracts": ["CW102136", "CW107764", "CW99034"],
        "cross_reseller": False,
        "note": "Carahsoft all three times",
    },
    "121-0218": {
        "product": "Salesforce Shield",
        "contracts": ["CW102136", "CW107764", "CW99034"],
        "cross_reseller": False,
        "note": "Carahsoft all three times",
    },
}

#: Tokens that look like manufacturer part numbers and are not. Kept because
#: each one cost a check, and the next person will hit them too.
FALSE_PART_NUMBERS = {
    "BMC91": "FMCSA insurance filing named in the standard insurance clause (19 contracts)",
    "BMC91X": "the same clause",
    "CG2037": "ISO additional-insured endorsement form CG 20 37, same clause (21 contracts)",
    "AR3227": "a DC contract number, CW77668-AR3227, not a Cisco part",
    "AR2472": "a DC contract number",
    "SF-1444": "the federal wage-determination request form",
    "GS-35F-0119Y": "a GSA schedule contract number",
    "47QSWA18D008F": "a GSA schedule contract number",
    "2-354.13": "D.C. Official Code, the sole-source exception for software maintenance",
    "132-51": "a GSA Special Item Number; the whole 132-xx block is SINs",
}

#: The two cached DC Supply Schedule catalogs. Their price tables are laid out
#: differently and each needs its own reader, so they are named rather than
#: detected.
CATALOGS = {
    "CW84401": "Stockbridge Consulting LLC",
    "CW94147": "Gatekeepers Internet Marketing, Inc.",
}

MONEY = re.compile(r"^\d{1,3}(,\d{3})*\.\d{2}$")


def document_owners() -> dict[str, tuple[str, str]]:
    """Map the digest a cached text is filed under to (contract number, name).

    Texts are cached by ``_digest(document_id)`` and the ids live only in
    ``contract_details.json``, so the mapping has to be rebuilt by hashing every
    id in that file. 1,380 of the 1,425 cached texts match; the rest belong to
    solicitations rather than contracts.
    """
    details = json.loads(DETAILS_PATH.read_text(encoding="utf-8"))
    owners: dict[str, tuple[str, str]] = {}
    for record in details:
        number = record.get("contractNumber")
        for document in record.get("documents") or []:
            owners.setdefault(_digest(document["id"]), (number, document["name"]))
    return owners


def cached_texts() -> dict[str, list[tuple[str, str]]]:
    """Every cached text, grouped by the contract it belongs to."""
    owners = document_owners()
    texts: dict[str, list[tuple[str, str]]] = {}
    for directory in TEXT_DIRS:
        for path in sorted(directory.glob("*.txt.gz")):
            digest = path.name[: -len(".txt.gz")]
            if digest not in owners:
                continue
            number, name = owners[digest]
            body = gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
            texts.setdefault(number, []).append((name, body))
    return texts


def part_tokens(text: str) -> set[str]:
    """Part-number-shaped tokens in one document."""
    found = set()
    for match in TOKEN.finditer(text):
        token = match.group(1).rstrip(".")
        if len(token) < 5 or SECTION.match(token):
            continue
        if token.startswith(NOT_PART_PREFIXES):
            continue
        if re.fullmatch(r"[A-Z]?\d{1,4}[A-Z]?", token):
            continue
        if re.search(r"\d{1,2}/\d{1,2}", token):
            continue
        found.add(token)
    return found


def token_frequency(texts: dict[str, list[tuple[str, str]]]) -> pd.Series:
    """How many distinct contracts each token appears in."""
    counter: collections.Counter = collections.Counter()
    per_contract = {
        number: set().union(*(part_tokens(body) for _, body in docs))
        for number, docs in texts.items()
    }
    for tokens in per_contract.values():
        counter.update(tokens)
    series = pd.Series(counter, name="contracts").sort_values(ascending=False)
    series.index.name = "token"
    return series


def shared_tokens(texts: dict[str, list[tuple[str, str]]]) -> pd.DataFrame:
    """Every token appearing in two or more contracts, with the contracts.

    This is the adjudication worklist. It is written out rather than filtered
    because the filter that separates ``KMSATD-N-J12-G`` from ``2-354.13`` is a
    person who knows what a D.C. Code cite looks like.
    """
    per_contract = {
        number: set().union(*(part_tokens(body) for _, body in docs))
        for number, docs in texts.items()
    }
    where: dict[str, list[str]] = collections.defaultdict(list)
    for number, tokens in per_contract.items():
        for token in tokens:
            where[token].append(number)
    rows = [
        {
            "token": token,
            "contracts": len(numbers),
            "where": ",".join(sorted(numbers)),
            "adjudicated_product": REPEATED_PRODUCTS.get(token, {}).get("product", ""),
            "known_false_positive": FALSE_PART_NUMBERS.get(token, ""),
        }
        for token, numbers in where.items()
        if len(numbers) >= 2
    ]
    return pd.DataFrame(rows).sort_values("contracts", ascending=False)


def priced_lines(texts: dict[str, list[tuple[str, str]]], token: str) -> pd.DataFrame:
    """Quantity and unit price wherever a given token is printed near money.

    A deliberately narrow reader: it looks for the token followed, within 200
    characters of whitespace-squeezed text, by a quantity and a dollar unit
    price. Award price schedules print them in that order. It is not a general
    extractor and will miss column-major tables entirely; that is why the
    :data:`REPEATED_PRODUCTS` comparisons are also printed as raw excerpts.
    """
    classified = pd.read_csv(ROOT / "data" / "contract_classifications.csv").set_index(
        "contract_number"
    )
    rows = []
    for number, documents in texts.items():
        for name, body in documents:
            flat = re.sub(r"\s+", " ", body)
            for match in re.finditer(re.escape(token), flat):
                window = flat[match.end() : match.end() + 200]
                # An optional unit-of-measure word sits between the quantity
                # and the price on some schedules ("36,000 each $3.06").
                priced = re.search(
                    r"([\d,]{1,9})\s+(?:[A-Za-z]+\s+){0,2}"
                    r"\$ ?([\d,]+\.\d{2})\s+\$ ?([\d,]+\.\d{2})",
                    window,
                )
                if not priced:
                    continue
                detail = classified.loc[number] if number in classified.index else None
                rows.append(
                    {
                        "contract": number,
                        "vendor": detail.vendor if detail is not None else "",
                        "agency": detail.agency if detail is not None else "",
                        "quantity": priced.group(1),
                        "unit_price": float(priced.group(2).replace(",", "")),
                        "extended_price": float(priced.group(3).replace(",", "")),
                        "document": name[:70],
                    }
                )
    return pd.DataFrame(rows).drop_duplicates(
        subset=["contract", "quantity", "unit_price"]
    )


def stockbridge_catalog(text: str) -> dict[str, float]:
    """Base-year prices from CW84401's catalog page.

    The page is column-major and perfectly regular: a block of manufacturer
    names, a block of SINs, a block of part numbers labelled ``PN``, then five
    price blocks headed ``Base Year`` and ``Option Yr 1..4``, each one longer
    than the part-number block by exactly its header. That regularity is what
    makes the pairing safe -- the same reconstruct-from-layout defence
    ``extract_rates.py`` uses on CW91684 -- and the length check is enforced
    rather than assumed.

    Only one page of the seventy-page attachment is in the text cache, so this
    yields 67 part numbers, not the whole catalog.
    """
    prices: dict[str, float] = {}
    for page in text.split("\f"):
        blocks = [
            [line.strip() for line in block.splitlines() if line.strip()]
            for block in re.split(r"\n\s*\n", page)
        ]
        parts = None
        for index, block in enumerate(blocks):
            if index + 1 < len(blocks) and blocks[index + 1] == ["PN"]:
                parts = block
            if not block or block[0] != "Base Year" or parts is None:
                continue
            values = block[1:]
            if len(values) != len(parts) or not all(MONEY.match(v) for v in values):
                continue
            for part, value in zip(parts, values):
                prices.setdefault(part, float(value.replace(",", "")))
    return prices


def gatekeepers_catalog(text: str, wanted: set[str]) -> dict[str, list[float]]:
    """The five price columns CW94147 prints against a part number.

    Row-major but mangled: the description column is duplicated and truncated,
    and the five prices follow it as ``$ n $ n $ n $ n n``. The page header
    reads "Item No. / Item Type / Base Year / Description / Option Year 1..4",
    so column one should be the base year -- but the printed columns step up by
    30% and then 50% between the first three, which is not an escalation
    schedule any contract would carry. Either the text layer has reordered the
    columns or the catalog really is priced that way, and a mangled dump cannot
    tell which. That is why this returns all five columns and no single price.
    """
    flat = re.sub(r"\s+", " ", text)
    found: dict[str, list[float]] = {}
    for part in wanted:
        for match in re.finditer(re.escape(part) + r"\s", flat):
            window = flat[match.start() : match.start() + 260]
            values = re.findall(r"\$ ([\d,]+\.\d{2})", window)
            if len(values) >= 4:
                found.setdefault(part, [float(v.replace(",", "")) for v in values])
                break
    return found


def dcss_catalog_overlap(texts: dict[str, list[tuple[str, str]]]) -> pd.DataFrame:
    """Part numbers priced by both cached DC Supply Schedule holders.

    The one place in this corpus where the same manufacturer's part is priced
    by more than one vendor. Read the caveats printed with it before reading
    the numbers.
    """
    stock = stockbridge_catalog(texts["CW84401"][0][1])
    gate = gatekeepers_catalog(texts["CW94147"][0][1], set(stock))
    rows = []
    for part in sorted(set(stock) & set(gate)):
        columns = gate[part]
        rows.append(
            {
                "part_number": part,
                "stockbridge_base_year": stock[part],
                "gatekeepers_col1": columns[0],
                "gatekeepers_col2": columns[1],
                "gatekeepers_col3": columns[2],
                "ratio_to_col1": round(stock[part] / columns[0], 2),
                "ratio_to_col2": round(stock[part] / columns[1], 2),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    texts = cached_texts()
    classified = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    technology = set(classified[classified.is_technology.map(bool)].contract_number)

    documents = sum(len(v) for v in texts.values())
    with_vocabulary = {
        number
        for number, docs in texts.items()
        if any(PART_VOCABULARY.search(body) for _, body in docs)
    }

    print("=" * 78)
    print("1. HOW MUCH MATERIAL THERE IS")
    print("=" * 78)
    print(f"cached texts matched to a contract      {documents}")
    print(f"contracts they cover                    {len(texts)}")
    print(f"  of which is_technology                {len(set(texts) & technology)}")
    print(f"contracts printing part-number language {len(with_vocabulary)}")

    frequency = token_frequency(texts)
    counts = frequency.value_counts().sort_index()
    print("\n" + "=" * 78)
    print("2. DO PART NUMBERS REPEAT ACROSS CONTRACTS?")
    print("=" * 78)
    print(f"distinct part-number-shaped tokens      {frequency.size}")
    print(f"  appearing in exactly one contract     {counts.get(1, 0)}")
    print(f"  in two contracts                      {counts.get(2, 0)}")
    print(f"  in three                              {counts.get(3, 0)}")
    print(f"  in four or more                       {int(counts[counts.index >= 4].sum())}")

    shared = shared_tokens(texts)
    shared.to_csv(OUT / "shared_tokens.csv", index=False)
    print(f"\nWrote the {len(shared)} multi-contract tokens to shared_tokens.csv.")
    print("Adjudicating them by hand leaves these as genuine repeated products:\n")
    products = pd.DataFrame(REPEATED_PRODUCTS).T
    products.index.name = "part_number"
    print(products.to_string())
    products.to_csv(OUT / "repeated_products.csv")

    print("\nTokens that look like part numbers and are not:")
    for token, why in FALSE_PART_NUMBERS.items():
        print(f"  {token:16s} {why}")

    print("\n" + "=" * 78)
    print("3. THE ONE COMPARISON THAT CROSSES RESELLERS")
    print("=" * 78)
    knowbe4 = priced_lines(texts, "KMSATD-N-J12-G")
    print(knowbe4.to_string(index=False))
    knowbe4.to_csv(OUT / "knowbe4.csv", index=False)
    if len(knowbe4) >= 2:
        low, high = knowbe4.unit_price.min(), knowbe4.unit_price.max()
        print(
            f"\nSame SKU, same 36,000 seats, same agency, two resellers:"
            f" ${low:.2f} and ${high:.2f}, {100 * (high / low - 1):.1f}% apart,"
            "\ntwo years apart. Nothing here separates a reseller margin from two"
            "\nyears of list-price escalation, and one pair cannot be a spread."
        )

    print("\n" + "=" * 78)
    print("4. THE SAME PART NUMBER, TWO PRICES, INSIDE ONE AWARD")
    print("=" * 78)
    filenet = priced_lines(texts, "E0501LL")
    print(filenet.to_string(index=False))
    filenet.to_csv(OUT / "filenet.csv", index=False)
    print(
        "\nCW92388 is one vendor, one award, one day. The two prices differ because"
        "\nthe quantities differ. Quantity alone moves a unit price 25% here, which"
        "\nis the bar any cross-contract spread would have to clear."
    )

    print("\n" + "=" * 78)
    print("5. THE LEAD: DC SUPPLY SCHEDULE CATALOGS")
    print("=" * 78)
    overlap = dcss_catalog_overlap(texts)
    print(overlap.to_string(index=False))
    overlap.to_csv(OUT / "dcss_catalog_overlap.csv", index=False)
    print(
        "\nWhat this is NOT evidence of, in order of severity:"
        "\n  1. These are schedule ceiling prices. The contract says the holder"
        "\n     'shall honor the pricing of the GSA Contract' and that prices 'shall"
        "\n     be no greater than' the adopted federal contract. Nobody paid them."
        "\n  2. Which of the Gatekeepers columns is the base year is not settled."
        "\n     The header says column one, but the columns step +30% then +50%."
        "\n  3. Different awards: Stockbridge FY2020, Gatekeepers April 2022."
        "\n  4. Only one page of Stockbridge's seventy-page catalog is cached, so"
        f"\n     the overlap is {len(overlap)} parts, all 3M screen filters."
        "\n  Resolving 1 and 2 needs the uncached catalog pages, not more code."
    )

    print(f"\nWrote CSVs to {OUT}")


if __name__ == "__main__":
    main()
