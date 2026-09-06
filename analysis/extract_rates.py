#!/usr/bin/env python3
"""Extract published hourly labor rates from cached contract award documents.

DC's staff-augmentation awards publish their price schedules: labor categories
with not-to-exceed hourly rates, usually laid out as a table the PDF text dump
mangles into interleaved runs of category names, skill levels, "$" signs and
bare numbers. An LLM reads that mangled text back into structured rows; every
extracted rate is then verified mechanically -- the number, formatted as its
digits, must appear verbatim in the document text -- and anything that fails is
dropped and counted. The LLM proposes, the document disposes.

Scope: technology contracts (``data/contract_classifications.csv``,
``is_technology``) whose award document text is already cached in
``data/pdftext``. Nothing is fetched from the network; contracts whose award
text is not cached are skipped.

Only per-hour prices for a person's time are kept. Unit prices for goods,
monthly or annual fees, percentages and line-item totals are excluded by the
prompt and, where the model slips, by the unit field and verification.

Verifying the number is not verifying the *pairing*: cross-validation caught
the model shifting rates one category over on CW91684's column-major pages
while every number still verified. Two defenses came out of that. CW91684
itself is reconstructed deterministically from its regular page layout (see
:func:`reconstruct_gold`), and every row carries a ``pairing`` column:
``deterministic`` (rebuilt from layout), ``proximate`` (category name and rate
digits printed within 250 characters of each other, as row-oriented tables
have them), or ``unverified`` (the association rests on the model alone --
typical for column-major dumps). A monotonicity report lists levelled
categories whose rates fall as seniority rises, the signature of a slipped
pairing; it is printed, never auto-fixed.

A built-in gold check hard-fails the run if CW91684 (Computer Aid's IT staff
augmentation award, hand-verified: 180 rates, min 24.01, max 173.71, median
69.97) stops coming out exactly.

LLM responses are cached by content hash in ``data/ratecache/`` so re-runs are
free. Requires ``OPENAI_API_KEY`` in the environment or the project ``.env``.

    uv run python analysis/extract_rates.py
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import re
import statistics
import sys
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.classify import DEFAULT_MODEL, _load_dotenv  # noqa: E402
from dcprocure.documents import (  # noqa: E402
    MIN_TEXT_CHARS,
    _digest,
    _text_path,
    award_document_index,
    pick_award_document,
)

log = logging.getLogger(__name__)

DETAILS_PATH = ROOT / "data" / "raw" / "contract_details.json"
LABELS_PATH = ROOT / "data" / "contract_classifications.csv"
CACHE_DIR = ROOT / "data" / "ratecache"
OUT_PATH = ROOT / "data" / "labor_rates.csv"

#: OCR fallback texts for scanned awards, same sha1-of-document-id naming as
#: data/pdftext. Used ONLY where the native text layer is missing or unusable,
#: never in place of a native text: the OCR text is itself the output of a
#: recognition model, so for these documents "verbatim verification" verifies
#: each rate against the OCR's reading of the page, not against the original
#: pixels -- one more model layer between the published number and the CSV,
#: recorded per row in the ``text_source`` column ("native" or "ocr").
OCR_TEXT_DIR = ROOT / "data" / "pdftext_ocr"


def load_award_text(document_id: str) -> tuple[str, str] | None:
    """The best cached text for a document: (text, source) or None.

    Native extraction wins whenever it is usable; the OCR fallback only fills
    in for scanned awards with no text layer. Nothing is ever fetched.
    """
    native = _text_path(document_id)
    if native.exists():
        text = gzip.decompress(native.read_bytes()).decode("utf-8")
        if len(text) >= MIN_TEXT_CHARS:
            return text, "native"
    ocr = OCR_TEXT_DIR / f"{_digest(document_id)}.txt.gz"
    if ocr.exists():
        text = gzip.decompress(ocr.read_bytes()).decode("utf-8")
        if len(text) >= MIN_TEXT_CHARS:
            return text, "ocr"
    return None


#: A document name carries the option period it is filed under, as in
#: ``CW107442-Option 3-Contract Award-...``. Removing it leaves a name that
#: identifies the underlying document rather than the copy.
_PERIOD_IN_NAME = re.compile(r"-(?:Base Period|Option \d+)-")


def award_document_copies(details: list[dict]) -> dict[str, list[dict[str, str]]]:
    """Every filed copy of each contract's award document.

    The executed award is re-filed under each option period and each copy is a
    separate document with its own id. The text caches are keyed by that id, so
    whether an award's text is on disk depends on which copy was read, not on
    whether the award has ever been read. This extractor never fetches, so a
    copy nothing has read is a copy with no text at all.
    """
    copies: dict[str, list[dict[str, str]]] = {}
    for record in details:
        number = record.get("contractNumber")
        document = pick_award_document(record)
        if number and document is not None:
            copies.setdefault(number, []).append(document)
    return copies


def load_any_copy(document: dict[str, str],
                  copies: list[dict[str, str]]) -> tuple[str, str] | None:
    """Cached text for an award, falling back to other copies of it.

    Only copies whose name matches once the option period is taken out of it. A
    later period can attach a genuinely different award, and reading that one
    instead would file another document's prices under this contract number.
    """
    loaded = load_award_text(document["id"])
    if loaded is not None:
        return loaded
    wanted = _PERIOD_IN_NAME.sub("-", document["name"])
    for other in copies:
        if other["id"] == document["id"]:
            continue
        if _PERIOD_IN_NAME.sub("-", other["name"]) != wanted:
            continue
        loaded = load_award_text(other["id"])
        if loaded is not None:
            return loaded
    return None


# Plausible hourly rates for a person's time. Below 15 is minimum-wage
# territory and mostly unit prices for goods; above 500/hour nobody bills
# through a DC staff-augmentation schedule.
RATE_MIN, RATE_MAX = 15.0, 500.0

# Ceiling for a *verified* rate. Looser than RATE_MAX because the District
# genuinely publishes four-figure hourly rates (CW134571 lists an IT Subject
# Matter Expert at a fully burdened $1,015.00/hour), but CW129102's "LABOR
# HOUR PRICE SCHEDULE" also prices "AWS Landing Zone Setup" at $25,028.58 for
# exactly 1 "hour" -- a fixed fee wearing an hourly-rate column header. No
# person's hour costs more than this.
RATE_KEEP_MAX = 1500.0

#: A number shaped like a price: optional thousands separators, two decimals.
MONEY = re.compile(r"\b\d{1,3}(?:,\d{3})*\.\d{2}\b")

#: Language that price schedules use and other sections do not.
RATE_LANGUAGE = re.compile(r"hourly rate|labor categor|fully burdened|per hour", re.I)

#: Evidence that a document prices anything by the hour at all: hourly-rate
#: vocabulary, or "Hour" standing alone as a unit-of-measure column value.
#: A document with neither cannot support the claim that its price list is
#: hourly -- CW117438 publishes a "Price List" of engineer rates without ever
#: stating a unit, and boilerplate like "works less than 25 hours per week"
#: must not count -- so such documents are excluded outright and reported.
HOURLY_EVIDENCE = re.compile(
    r"(?im)hourly|per[- ]hour|labor[- ]hours?|/ ?hour|^\s*hours?\s*$"
)

#: A page needs this many in-range prices to look like part of a rate card.
PAGE_MIN_RATES = 4

#: Cheap pre-filter: a document plausibly carries a rate card when it either
#: talks like one or contains a cluster of at least this many in-range prices.
CLUSTER_MIN = 8

#: Prompt-size caps. Rate cards run a few pages; anything bigger is invoices.
EXCERPT_LIMIT = 40_000
PAGE_LIMIT = 15_000

#: At most this many price-bearing pages per document are read. Beyond this
#: the "pages" are burn tables or invoices, not a published rate card.
MAX_CHUNKS = 40

# Awards attach Department of Labor wage determinations: tables of *minimum
# wages owed to employees* ("14.61" for a Surveying Aide), which are hourly
# numbers against job titles but are wage floors, not prices the District
# pays -- and identical boilerplate across contracts. A page listing SCA
# occupation codes ("99310 - Mortician") or headed as a wage-determination
# register is dropped before the model ever sees it. The bare phrase "wage
# determination" is NOT enough: Section H boilerplate cites it on pages that
# also carry the real price schedule.
_WD_HEADER = re.compile(r"REGISTER OF WAGE DETERMINATIONS", re.I)
_OCC_CODE = re.compile(r"\b\d{5} - [A-Z]")
WD_OCC_MIN = 5


def is_wage_determination_page(page: str) -> bool:
    return bool(_WD_HEADER.search(page)) or len(_OCC_CODE.findall(page)) >= WD_OCC_MIN

# The gold contract: Computer Aid's IT staff augmentation award, whose price
# schedule was hand-verified (45 job categories x Entry/Journeyman/Senior/
# Master). If extraction stops reproducing it exactly, fix the extraction.
GOLD_CONTRACT = "CW91684"
GOLD = {"n": 180, "min": 24.01, "max": 173.71, "median": 69.97}

# CW91684 is not extracted by the LLM at all: cross-validation showed the
# model's category-rate PAIRING slips by one category partway down these
# column-major pages -- the numbers all verify verbatim and the aggregates are
# untouched (which is why the count/min/median/max gold check could not catch
# it), but 35 of 45 categories carried a neighbour's rates, betrayed by
# non-monotonic Entry<Journeyman<Senior<Master runs. The page layout is
# perfectly regular, so the card is reconstructed deterministically instead:
# category names appear in document order between the "Job Category" and
# "Skill Level" headers (four names wrap onto a continuation line and must be
# merged), the level labels form an exact (Entry, Journeyman, Senior, Master)
# cycle, and the k-th category owns rates 4k..4k+3 in stream order. The
# reconstruction is validated against five hand-read rows and by every one of
# the 45 categories being strictly monotonic in seniority.
GOLD_LEVELS = ("Entry", "Journeyman", "Senior", "Master")
GOLD_WRAPPED_NAMES = {
    "Enterprise Resource Planning (ERP)": "Analyst",
    "Geographic Information System (GIS)": "Analyst/Programmer",
    "Information Specialist/Knowledge": "Engineer",
    "Subject Matter Expert - Technology": "Specific",
}
_GOLD_RATE_LINE = re.compile(r"^\d{1,3}\.\d{2}$")


def reconstruct_gold(text: str) -> list[dict]:
    """Deterministically rebuild CW91684's rate card from its page layout."""
    categories: list[str] = []
    levels: list[str] = []
    rates: list[float] = []
    for page in text.split("\f"):
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        if "Job Category" not in lines or "Skill Level" not in lines:
            continue
        cat_lines = lines[lines.index("Job Category") + 1 : lines.index("Skill Level")]
        i = 0
        while i < len(cat_lines):
            name = cat_lines[i]
            if i + 1 < len(cat_lines) and GOLD_WRAPPED_NAMES.get(name) == cat_lines[i + 1]:
                name = f"{name} {cat_lines[i + 1]}"
                i += 1
            categories.append(name)
            i += 1
        for line in lines:
            if line in GOLD_LEVELS:
                levels.append(line)
            elif _GOLD_RATE_LINE.match(line):
                rates.append(float(line))

    if not (
        len(categories) == 45
        and len(rates) == 180
        and levels == list(GOLD_LEVELS) * 45
    ):
        raise SystemExit(
            f"{GOLD_CONTRACT} reconstruction broke: {len(categories)} categories, "
            f"{len(rates)} rates, level cycle {'ok' if levels == list(GOLD_LEVELS) * 45 else 'BROKEN'}"
        )
    rows = [
        {"labor_category": cat, "level": level, "hourly_rate": rate}
        for k, cat in enumerate(categories)
        for level, rate in zip(GOLD_LEVELS, rates[4 * k : 4 * k + 4])
    ]
    by_cat = {cat: rates[4 * k : 4 * k + 4] for k, cat in enumerate(categories)}
    if not all(a < b < c < d for a, b, c, d in by_cat.values()):
        raise SystemExit(f"{GOLD_CONTRACT} reconstruction is not monotonic in seniority")
    # Hand-read anchor rows. Enterprise Architect is where the LLM's slipped
    # pairing was first caught (it had assigned 54.01 to Entry).
    anchors = {
        "Enterprise Architect": [75.83, 88.73, 98.92, 138.47],
        "System Administrator": [49.76, 57.99, 64.70, 70.37],
    }
    for cat, expected in anchors.items():
        if by_cat.get(cat) != expected:
            raise SystemExit(
                f"{GOLD_CONTRACT} reconstruction disagrees with the hand-read "
                f"{cat} row: {by_cat.get(cat)} != {expected}"
            )
    return rows

SYSTEM_PROMPT = """\
You are reading the price schedule of a District of Columbia government \
contract. The text is a mangled dump of a PDF table: labor category names, \
skill levels, "$" signs and bare numbers often appear on separate lines, in \
runs, out of visual order. Reconstruct the table.

Extract every published HOURLY labor rate: a per-hour price for a person's \
time, listed against a labor category (job title), possibly with a skill \
level (e.g. Entry, Journeyman, Senior, Master, Level I/II/III).

Rules:
- Only per-hour prices for labor. Set unit to "hour" for each.
- EXCLUDE unit prices for goods, licenses or equipment; monthly, annual or \
flat fees; percentages; extended prices, line-item totals, ceilings and \
not-to-exceed contract amounts; quantities and hour counts; and minimum wages \
from Department of Labor wage determinations (those are wage floors owed to \
employees, not prices the government pays).
- A run of bare numbers after a run of category names and skill levels is \
usually the rate column read column-wise: match them up in order. Every \
skill-level entry has exactly one rate, so the counts must line up.
- Copy each rate exactly as printed (same digits). Do not compute, sum or \
round anything.
- labor_category is the job title; level is the skill level or tier, or "" \
if none is given. If the same category repeats for several periods (base \
year, option years), extract every printed rate as its own entry.
- If the text contains no hourly labor rates, return an empty list."""

USER_TEMPLATE = """Contract title: {title}
Vendor: {vendor}

Excerpt from the contract document ([...] marks elided text):

{excerpt}

Extract the hourly labor rates."""


class HourlyRate(BaseModel):
    labor_category: str = Field(description="Job title / labor category as printed")
    level: str = Field(default="", description='Skill level or tier; "" if none')
    hourly_rate: float = Field(description="The published rate, exactly as printed")
    unit: str = Field(description='Billing unit; "hour" for an hourly labor rate')


class RateCard(BaseModel):
    rates: list[HourlyRate] = Field(description="Every published hourly labor rate")


def in_range_prices(text: str) -> int:
    """How many price-shaped numbers in the text are plausible hourly rates."""
    return sum(
        1 for m in MONEY.findall(text) if RATE_MIN <= float(m.replace(",", "")) <= RATE_MAX
    )


def looks_like_rate_card(text: str) -> bool:
    """Cheap pre-filter: worth sending to the model at all?"""
    return bool(RATE_LANGUAGE.search(text)) or in_range_prices(text) >= CLUSTER_MIN


def _condense(page: str, limit: int = PAGE_LIMIT) -> str:
    """Shrink an oversized page to windows around its price clusters.

    Documents extracted without page breaks arrive as one huge "page";
    invoices and burn tables can also inflate a real one. Keeping generous
    context before each price preserves the category names, which the table
    layout puts ahead of the numbers.
    """
    if len(page) <= limit:
        return page
    spans: list[list[int]] = []
    for match in MONEY.finditer(page):
        if not RATE_MIN <= float(match.group().replace(",", "")) <= RATE_MAX:
            continue
        start, stop = max(0, match.start() - 2000), min(len(page), match.end() + 400)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = stop
        else:
            spans.append([start, stop])
    if not spans:
        return page[:limit]
    return "\n[...]\n".join(page[a:b] for a, b in spans)[:limit]


def rate_chunks(text: str) -> list[str]:
    """The parts of a document worth showing the model, one call apiece.

    pdftotext emits form feeds between pages, and rate cards are page-shaped:
    a page holding a cluster of plausible hourly prices is (part of) the price
    schedule, categories and rates together on the one page. Each such page
    becomes its own LLM call -- reconstructing one page's table (~30 rates) is
    a task the model gets exactly right where a whole schedule (180 rates) is
    not. Documents that merely talk like a price schedule get a single call
    over the pages that do the talking.
    """
    pages = [p for p in text.split("\f") if not is_wage_determination_page(p)]
    rate_pages = [
        _condense(p) for p in pages if in_range_prices(p) >= PAGE_MIN_RATES
    ]
    if rate_pages:
        return rate_pages[:MAX_CHUNKS]
    # Pre-filter fired on language alone; one call over those pages (or the
    # head, when the language is scattered) catches schedules the page
    # heuristic misses.
    talking = [p for p in pages if RATE_LANGUAGE.search(p)][:3]
    excerpt = "\n\n[...]\n\n".join(_condense(p) for p in talking)
    return [excerpt if excerpt.strip() else _condense(text, EXCERPT_LIMIT // 4)]


def _cache_key(model: str, excerpt: str) -> str:
    prompt_print = hashlib.sha1(SYSTEM_PROMPT.encode()).hexdigest()[:12]
    return hashlib.sha1(f"{model}\x00{prompt_print}\x00{excerpt}".encode()).hexdigest()


async def _extract_one(
    record: dict,
    model: str,
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> list[dict] | None:
    """One cached LLM call: mangled excerpt in, structured rate list out.

    Same shape as :func:`dcprocure.classify._classify_one`; standalone because
    the classify machinery returns one flat dict per record, not a list.
    """
    import litellm

    litellm.drop_params = True
    litellm.suppress_debug_info = True

    key = _cache_key(model, record["excerpt"])
    cached = CACHE_DIR / f"{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))

    user = USER_TEMPLATE.format(
        title=record["title"], vendor=record["vendor"], excerpt=record["excerpt"]
    )
    async with semaphore:
        for attempt in range(max_retries):
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user},
                    ],
                    response_format=RateCard,
                    temperature=0.0,
                    timeout=300,
                )
                parsed = RateCard(**json.loads(response.choices[0].message.content))
                rates = [r.model_dump(mode="json") for r in parsed.rates]
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(rates, indent=1), encoding="utf-8")
                return rates
            except Exception as exc:
                if attempt == max_retries - 1:
                    log.warning("extraction failed for %s: %s", record["contract_number"], exc)
                    return None
                await asyncio.sleep(2**attempt)
    return None


async def extract_all(records: list[dict], model: str, concurrency: int = 8) -> list[list[dict] | None]:
    semaphore = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*(_extract_one(r, model, semaphore) for r in records))


def _verbatim(rate: float, text: str) -> bool:
    """Whether the rate's digits appear verbatim in the document text.

    "71.92" must appear as 71.92 (or 1,234.56 with separators), not embedded
    in a longer number: neither 171.92 nor 55,475.00 verifies 475.00, which is
    why the lookbehind also rejects a digit-comma prefix. This is the
    defensibility step: a rate the document does not print is not a rate the
    document published.
    """
    plain = f"{rate:.2f}"
    for variant in {plain, f"{rate:,.2f}"}:
        if re.search(rf"(?<!\d,)(?<![\d.]){re.escape(variant)}(?!\d)", text):
            return True
    return False


def verify_rates(rates: list[dict], text: str) -> tuple[list[dict], int]:
    """Keep only hourly rates the document verifiably prints."""
    kept, dropped = [], 0
    seen: set[tuple[str, str, float]] = set()
    for rate in rates:
        value = rate.get("hourly_rate")
        unit = (rate.get("unit") or "").lower()
        triple = (rate.get("labor_category", ""), rate.get("level", ""), value)
        if (
            not isinstance(value, (int, float))
            or not ("hour" in unit or unit in ("hr", "hrly"))
            or not RATE_MIN <= float(value) <= RATE_KEEP_MAX  # fee, not labor
            or _OCC_CODE.match(rate.get("labor_category", ""))  # SCA wage floor
            or triple in seen  # model echo, not a second published rate
            or not _verbatim(float(value), text)
        ):
            dropped += 1
            continue
        seen.add(triple)
        kept.append(rate)
    return kept, dropped


# Verification proves each NUMBER is printed; it cannot prove the number
# belongs to the category the model put it against. The pairing check gets at
# that mechanically: a row is "proximate" when the category string and the
# rate's digits occur within PAIR_WINDOW characters of each other somewhere in
# the document, which row-oriented tables satisfy and column-major dumps (a
# page of names followed by a page-length run of numbers) mostly do not.
# Failing rows keep their data but are marked "unverified" -- the association
# rests on the model alone.
PAIR_WINDOW = 250
FUZZY_MIN = 0.85


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _category_spans(category: str, normdoc: str) -> list[tuple[int, int]]:
    """Where the category name occurs in the normalized document.

    Exact substring match first; models sometimes normalize a wrapped or
    hyphenated name, so a best-window fuzzy match >= FUZZY_MIN backs it up.
    """
    needle = _normalize(category)
    if not needle:
        return []
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(needle), normdoc)]
    if spans:
        return spans
    import difflib

    width = len(needle)
    step = max(1, width // 5)
    matcher = difflib.SequenceMatcher(b=needle, autojunk=False)
    best: list[tuple[int, int]] = []
    for start in range(0, max(1, len(normdoc) - width + 1), step):
        matcher.set_seq1(normdoc[start : start + width])
        if matcher.real_quick_ratio() >= FUZZY_MIN and matcher.ratio() >= FUZZY_MIN:
            best.append((start, start + width))
    return best


def _rate_spans(rate: float, normdoc: str) -> list[tuple[int, int]]:
    spans = []
    for variant in {f"{rate:.2f}", f"{rate:,.2f}"}:
        spans += [
            (m.start(), m.end())
            for m in re.finditer(
                rf"(?<!\d,)(?<![\d.]){re.escape(variant)}(?!\d)", normdoc
            )
        ]
    return spans


def _min_gap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> int | None:
    """Smallest character gap between any span of a and any span of b."""
    if not a or not b:
        return None
    return min(
        max(b1 - a2, a1 - b2, 0) for a1, a2 in a for b1, b2 in b
    )


def pairing_status(
    category: str,
    rate: float,
    normdoc: str,
    span_cache: dict[str, list[tuple[int, int]]],
) -> str:
    key = f"c\x00{_normalize(category)}"
    if key not in span_cache:
        span_cache[key] = _category_spans(category, normdoc)
    rkey = f"r\x00{rate:.2f}"
    if rkey not in span_cache:
        span_cache[rkey] = _rate_spans(rate, normdoc)
    gap = _min_gap(span_cache[key], span_cache[rkey])
    return "proximate" if gap is not None and gap <= PAIR_WINDOW else "unverified"


#: Seniority ladders, for the monotonicity sanity report. A category's rates
#: should not fall as its level rises; where they do, the pairing is suspect.
LEVEL_RANK = {
    "entry": 0, "junior": 0, "jr": 0, "i": 0,
    "journeyman": 1, "intermediate": 1, "mid": 1, "ii": 1,
    "senior": 2, "sr": 2, "iii": 2,
    "master": 3, "expert": 3, "iv": 3,
    "v": 4,
    "vi": 5,
}


def monotonicity_report(frame: pd.DataFrame) -> list[str]:
    """Categories whose median rate does not rise with seniority.

    Medians per level, because the same level legitimately carries several
    rates (base year and option years). Reported, never auto-fixed.
    """
    violations: list[str] = []
    levelled = frame[frame.level.notna() & (frame.level.astype(str).str.strip() != "")]
    for (contract, category), group in levelled.groupby(["contract_number", "labor_category"]):
        medians: dict[int, float] = {}
        for level, rates in group.groupby(group.level.astype(str).str.strip().str.lower()):
            if level in LEVEL_RANK:
                medians[LEVEL_RANK[level]] = rates.hourly_rate.median()
        if len(medians) < 2:
            continue
        ordered = [medians[r] for r in sorted(medians)]
        if any(b < a for a, b in zip(ordered, ordered[1:])):
            violations.append(
                f"  {contract} {category}: "
                + " -> ".join(f"{v:.2f}" for v in ordered)
            )
    return violations


def gold_check(rows: pd.DataFrame) -> None:
    """Hard-fail unless the hand-verified contract reproduces exactly."""
    got = rows.loc[rows.contract_number == GOLD_CONTRACT, "hourly_rate"]
    actual = {
        "n": len(got),
        "min": round(got.min(), 2) if len(got) else None,
        "max": round(got.max(), 2) if len(got) else None,
        "median": round(statistics.median(got), 2) if len(got) else None,
    }
    if actual != GOLD:
        raise SystemExit(
            f"GOLD CHECK FAILED for {GOLD_CONTRACT}: expected {GOLD}, got {actual}. "
            "Fix the extraction, not the check."
        )
    print(f"gold check passed: {GOLD_CONTRACT} -> {GOLD['n']} rates, "
          f"min {GOLD['min']}, median {GOLD['median']}, max {GOLD['max']}")


def main(model: str = DEFAULT_MODEL) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    _load_dotenv()

    labels = pd.read_csv(LABELS_PATH)
    tech = labels[labels.is_technology == True]  # noqa: E712
    _details_raw = json.loads(DETAILS_PATH.read_text(encoding="utf-8"))
    details = {d["contractNumber"]: d for d in _details_raw}
    # The award may be filed under an option period the collapse above discards.
    awards = award_document_index(_details_raw)
    copies = award_document_copies(_details_raw)

    # Candidate set: technology contracts whose award text is already cached.
    scanned, scanned_ocr, candidates = 0, 0, []
    no_hour_evidence: list[str] = []
    gold_candidate: dict | None = None
    for label in tech.itertuples():
        detail = details.get(label.contract_number)
        document = awards.get(label.contract_number)
        if document is None:
            continue
        loaded = load_any_copy(document, copies.get(label.contract_number, []))
        if loaded is None:
            continue  # no usable cached text, native or OCR; never fetch
        text, text_source = loaded
        scanned += 1
        if text_source == "ocr":
            scanned_ocr += 1
        if not looks_like_rate_card(text):
            continue
        if not HOURLY_EVIDENCE.search(text):
            no_hour_evidence.append(label.contract_number)
            continue
        candidate = {
            "contract_number": label.contract_number,
            "title": label.title,
            "vendor": label.vendor,
            "work_type": label.work_type,
            "ceiling": label.ceiling,
            "text": text,
            "text_source": text_source,
            "chunks": rate_chunks(text),
        }
        if label.contract_number == GOLD_CONTRACT:
            gold_candidate = candidate  # reconstructed, not sent to the model
            continue
        candidates.append(candidate)
    calls = [
        {**{k: c[k] for k in ("contract_number", "title", "vendor")}, "excerpt": chunk}
        for c in candidates
        for chunk in c["chunks"]
    ]
    print(f"{scanned:,} technology contracts have cached award text; "
          f"{len(candidates):,} look like they carry a rate card "
          f"({len(calls):,} price-bearing pages)")
    if no_hour_evidence:
        print(f"excluded {len(no_hour_evidence)} whose document never prices "
              f"anything by the hour: {', '.join(no_hour_evidence)}")

    print(f"extracting rates with {model}")
    extractions = asyncio.run(extract_all(calls, model))

    meta = {c["contract_number"]: c for c in candidates}
    normdocs = {c["contract_number"]: _normalize(c["text"]) for c in candidates}
    span_caches: dict[str, dict] = {c["contract_number"]: {} for c in candidates}
    rows: list[dict] = []
    dropped_total = 0
    drops_by_contract: dict[str, int] = {}
    failed: set[str] = set()
    # Each page is verified separately, against the page the model actually
    # read: a triple repeated on one page is a model echo, but the same
    # category and rate on two pages is two published rates (base year and an
    # option year, typically).
    for call, extracted in zip(calls, extractions):
        contract = call["contract_number"]
        if extracted is None:
            failed.add(contract)
            continue
        kept, dropped = verify_rates(extracted, call["excerpt"])
        dropped_total += dropped
        if dropped:
            drops_by_contract[contract] = drops_by_contract.get(contract, 0) + dropped
        candidate = meta[contract]
        for rate in kept:
            value = float(rate["hourly_rate"])
            rows.append(
                {
                    "contract_number": contract,
                    "vendor": candidate["vendor"],
                    "title": candidate["title"],
                    "work_type": candidate["work_type"],
                    "ceiling": candidate["ceiling"],
                    "labor_category": rate["labor_category"].strip(),
                    "level": rate["level"].strip(),
                    "hourly_rate": value,
                    "pairing": pairing_status(
                        rate["labor_category"],
                        value,
                        normdocs[contract],
                        span_caches[contract],
                    ),
                    "text_source": candidate["text_source"],
                }
            )
    if failed:
        print(f"warning: extraction failed outright for {len(failed)}: "
              f"{', '.join(sorted(failed))}")

    # CW91684 comes from the deterministic page-cycle reconstruction, never
    # from the model -- see reconstruct_gold for why.
    if gold_candidate is not None:
        for rate in reconstruct_gold(gold_candidate["text"]):
            rows.append(
                {
                    "contract_number": GOLD_CONTRACT,
                    "vendor": gold_candidate["vendor"],
                    "title": gold_candidate["title"],
                    "work_type": gold_candidate["work_type"],
                    "ceiling": gold_candidate["ceiling"],
                    **rate,
                    "pairing": "deterministic",
                    "text_source": gold_candidate["text_source"],
                }
            )

    frame = pd.DataFrame(
        rows,
        columns=["contract_number", "vendor", "title", "work_type", "ceiling",
                 "labor_category", "level", "hourly_rate", "pairing", "text_source"],
    ).sort_values(["contract_number", "labor_category", "level", "hourly_rate"])

    gold_check(frame)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH} ({len(frame):,} rows)")

    with_rates = frame.contract_number.nunique()
    by_pairing = frame.pairing.value_counts()
    print(f"\ncontracts scanned:          {scanned:,} "
          f"({scanned - scanned_ocr:,} native text, {scanned_ocr:,} OCR fallback)")
    print(f"sent to the LLM:            {len(candidates):,} "
          f"(+ {GOLD_CONTRACT} reconstructed deterministically)")
    print(f"with >= 1 verified rate:    {with_rates:,}")
    print(f"verified rates:             {len(frame):,}")
    print("pairing:                    "
          + ", ".join(f"{s}: {by_pairing.get(s, 0):,}"
                      for s in ("deterministic", "proximate", "unverified")))
    ocr_rows = frame[frame.text_source == "ocr"]
    print(f"from OCR fallback text:     {len(ocr_rows):,} rates "
          f"across {ocr_rows.contract_number.nunique()} contracts "
          f"(verified against the OCR reading, not the original pixels)")
    if len(frame):
        print(f"hourly rate min/median/max: {frame.hourly_rate.min():.2f} / "
              f"{frame.hourly_rate.median():.2f} / {frame.hourly_rate.max():.2f}")
    print(f"dropped in verification:    {dropped_total:,}"
          + (f" ({', '.join(f'{k}: {v}' for k, v in sorted(drops_by_contract.items()))})"
             if drops_by_contract else ""))

    violations = monotonicity_report(frame)
    if violations:
        print(f"\n{len(violations)} categories where rates do not rise with "
              "seniority (pairing suspect; reported, not auto-fixed):")
        print("\n".join(violations))
    else:
        print("\nmonotonicity: every levelled category rises with seniority")

    if len(frame):
        per = (
            frame.groupby("contract_number")
            .agg(vendor=("vendor", "first"), n_rates=("hourly_rate", "size"),
                 min=("hourly_rate", "min"), median=("hourly_rate", "median"),
                 max=("hourly_rate", "max"))
        )
        counts = (
            frame.pivot_table(index="contract_number", columns="pairing",
                              aggfunc="size", fill_value=0)
            .reindex(columns=["deterministic", "proximate", "unverified"], fill_value=0)
        )
        per = per.join(counts).sort_values("n_rates", ascending=False)
        per = per[["vendor", "n_rates", "deterministic", "proximate", "unverified",
                   "min", "median", "max"]]
        with pd.option_context("display.max_rows", None, "display.width", 200,
                               "display.max_columns", None, "display.max_colwidth", 40):
            print(f"\n{per.round(2)}")
            if len(ocr_rows):
                print("\ncontracts read from OCR text only:")
                print(per.loc[per.index.isin(ocr_rows.contract_number.unique())].round(2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL))
