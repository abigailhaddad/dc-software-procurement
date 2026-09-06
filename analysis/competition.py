#!/usr/bin/env python3
"""Read how competitively DC awarded its technology contracts, out of the D&Fs.

Nothing in the published dataset records how an award was competed. The portal
carries a ``marketType`` field, and it does not answer the question: it takes
values like "Open" and "Set Aside" that describe the market a solicitation was
advertised to, and it keeps saying "Open" on contracts whose own paperwork says
the District went to one vendor.

The paperwork is on the portal and has never been opened. A Determination and
Findings is the memo a contracting officer signs to do something that needs
justifying -- award without competition, skip the DC Supply Schedule, ride
another jurisdiction's cooperative contract, start work under a letter
contract, or record that a price is reasonable and a contractor responsible.
``data/raw/contract_details.json`` lists 21,722 attachments in the "D&Fs"
category across 2,828 contracts; 5,300 of them sit on 1,009 technology
contracts. This script downloads a capped sample, reads the subtype out of the
document's own text, and writes a per-contract competition record.

Read the subtype from the TEXT, not the filename. Filenames are free text a
human typed into an upload form and they are wrong in both directions: 531 of
the 2,402 unique technology D&Fs carry a name with no subtype in it at all
("D&F.pdf", "D and F .pdf", "Approved-D and F.pdf"), and among the ones that
look informative the word is often misspelled ("Resposibility",
"Responsbility", "Reponsibility") or refers to a different document than the
one attached. The documents themselves are near-boilerplate: they open with
``DETERMINATION AND FINDINGS FOR <SUBTYPE>`` followed by a numbered AUTHORITY
clause citing the PPRA section relied on. :func:`subtype_from_text` reads that
header; the run reports how far it disagrees with the filename.

THREE TRAPS, all of which push the answer in the flattering direction:

* Scans. A large share of these PDFs have no text layer. An unreadable
  document must never be recorded as "no justification found", because the
  scan rate is highest exactly on the subtypes that matter -- so the missing
  rows are the ones that would strengthen the finding, not weaken it. Every
  document here lands in an explicit ``text_status`` and unreadable ones are
  counted separately from readable ones with no header.
* Mojibake. Some PDFs have a text layer whose fonts carry no character map, so
  the extraction is thousands of characters of garbage that passes any length
  check. :func:`dcprocure.documents.looks_unreadable` catches those; they are
  ``mojibake``, not ``readable``.
* "Non-Use of DCSS or SBE Set-Aside Market" is NOT by itself a statement that
  competition was limited -- it is a determination not to restrict the buy to
  the DC Supply Schedule or to certified small businesses, which can mean the
  opposite, going to the open market. It only evidences a single source when
  the document says so: when it cites D.C. Official Code § 2-354.13(8), the
  exception for maintenance of existing software where the creator of the
  intellectual property is the only source, or when its own text asserts one
  available vendor. Those are counted separately from the header subtype.

Downloads are capped (``--limit``, default 400) and prioritised: subtypes that
bear on competition first, uninformative filenames second, price
reasonableness third, contractor responsibility last. Reconnaissance found
responsibility D&Fs substantive but analytically empty -- a vendor's Dun &
Bradstreet date and its tax and wage compliance dates -- so they are sampled
only to measure how often the filename is right.

Text is cached in ``data/pdftext_df/<sha1 of document id>.txt.gz``, a separate
directory from ``data/pdftext/``. That matters: ``analysis/solicitations_pdfscan.py``
reads *every* cached text for a contract's documents, so writing D&F text into
the shared cache would silently change a published result.

No model calls. Everything here is regex over the text layer; scanned D&Fs are
counted and left unread rather than OCRed, because OCR costs money per page and
the scan rate is high.

    uv run python analysis/competition.py --enumerate       # counts, no network
    uv run python analysis/competition.py --limit 400       # download and read
    uv run python analysis/competition.py --report          # re-read the cache
    uv run python analysis/competition.py --task-orders 6   # peek inside task orders
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import logging
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.client import DCProcurementClient  # noqa: E402
from dcprocure.documents import (  # noqa: E402
    extract_text,
    looks_unreadable,
    other_contracts_named,
    readable_share,
)

log = logging.getLogger("competition")

DETAILS = ROOT / "data" / "raw" / "contract_details.json"
CLASSES = ROOT / "data" / "contract_classifications.csv"
TEXT_DIR = ROOT / "data" / "pdftext_df"
OUT_DIR = ROOT / "data" / "competition"
DOC_CSV = OUT_DIR / "df_documents.csv"
CONTRACT_CSV = OUT_DIR / "contract_competition.csv"
QUOTES_JSON = OUT_DIR / "quotes.json"

DEFAULT_LIMIT = 400
SEED = 20260905

# Attachment names are "CW#-<option period>-<category>-<free text>". The
# category is a fixed vocabulary; the free text is not.
DF_CATEGORY = "D&Fs"
TASK_ORDER_CATEGORY = "Task Orders"


# ---------------------------------------------------------------------------
# Filename tiers -- used ONLY to decide what to download, never to classify.
# ---------------------------------------------------------------------------

#: Names that promise something about competition. Deliberately broad: this is
#: a download priority, so a false positive costs one request and a false
#: negative costs a document that would have mattered.
NAME_COMPETITION = re.compile(
    r"sole\s*sourc|single\s*sourc|non-?competit|other than (?:full and )?open"
    r"|non-?use of|dcss|set-?aside|cooperativ|\bcoop\b|\bgsa\b|naspo|omnia"
    r"|sourcewell|letter contract|unusual and compelling|urgen|emergen"
    r"|proprietar|brand name|exemption|only source",
    re.I,
)
NAME_PRICE = re.compile(r"price|reasonab|\bfair\b", re.I)
NAME_RESPONSIBILITY = re.compile(r"respons\w*b|resposib|responsb|reponsib|\bresp\b|\bcr\b", re.I)

TIER_COMPETITION = "A_competition"
TIER_UNINFORMATIVE = "B_uninformative"
TIER_PRICE = "C_price"
TIER_RESPONSIBILITY = "D_responsibility"

#: How many documents each tier may claim of the download budget. Tier A takes
#: everything it has; the rest are capped so that a download budget spent on
#: contractor-responsibility memos does not crowd out the ones that carry the
#: finding. C and D are sampled at all only so the filename-versus-text
#: comparison has rows where the filename looked confident.
TIER_QUOTA = {TIER_COMPETITION: None, TIER_UNINFORMATIVE: 220, TIER_PRICE: 40,
              TIER_RESPONSIBILITY: 20}


def filename_tier(free_text: str) -> str:
    """Download priority for one D&F, from its filename alone."""
    if NAME_COMPETITION.search(free_text):
        return TIER_COMPETITION
    if NAME_PRICE.search(free_text):
        return TIER_PRICE
    if NAME_RESPONSIBILITY.search(free_text):
        return TIER_RESPONSIBILITY
    return TIER_UNINFORMATIVE


# ---------------------------------------------------------------------------
# Subtype, read from the document's own header.
# ---------------------------------------------------------------------------

#: The header line. "DETERMINATION AND FINDINGS FOR ..." is the standard form,
#: but the short DCSS waiver drops "AND FINDINGS" and some offices write
#: "DETERMINATIONS". The subtype phrase runs to the next all-caps field label
#: (AGENCY:, CONTRACT NO.:, FINDINGS) or to 80 characters, whichever is first;
#: pdftotext collapses these forms onto one line, so a newline is no boundary.
_LABEL = (
    r"FINDINGS?\b|AGENC(?:Y|IES)\s*:|CONTRACTS?\s*(?:NO\.?|NUMBER|#)"
    r"|CONTRACTING\s+(?:AGENCY|OFFICER)|CONTRACTOR\b|CAPTION\b|PROPOSED\b"
    r"|PROGRAM\s+AGENCY|RFP\b|IFB\b|TASK\s+ORDER\b|PURCHASE\s+ORDER|SOLICITATION\b"
    r"|PROCURING\b|DOCUMENT\b|REQUISITION\b|REQUIREMENT\b|AWARD(?:ED)?\s+(?:AMOUNT|VENDOR)"
    r"|D\.?C\.?\s+OFFICIAL|DATE\s*:|SUBJECT\s*:|MEMORANDUM\b"
)

#: The header, in its usual "... FOR <subtype>" form. The subtype phrase runs
#: to the next field label or to 100 characters, whichever is first; pdftotext
#: collapses these cover pages onto one line, so a newline is no boundary.
#:
#: THE TRAP in the label list: a bare ``CONTRACT\b`` stop truncates
#: "LETTER CONTRACT" to "LETTER" and loses the subtype, and a ``CONTRACT\b``
#: that does not also allow the plural fails to stop at "...SET-ASIDES OF
#: CONTRACTS UNDER $250,000", so the phrase overruns the length cap and the
#: header is missed entirely. Both were real: they cost 10 and 2 documents.
_HEADER = re.compile(
    r"DETERMINATIONS?\s*(?:AND\s+FINDINGS?\s*)?FOR\s+"
    r"(?:A\s+|AN\s+|THE\s+|USE\s+OF\s+(?:A\s+)?)?"
    r"(.{3,100}?)(?=\s+(?:" + _LABEL + r")|\s*$)",
    re.I,
)

#: Some offices drop the "FOR": "DETERMINATION AND FINDINGS PRICE
#: REASONABLENESS". Tried only when the FOR-form misses, and guarded so that a
#: bare "DETERMINATION AND FINDINGS" followed by the AGENCY: block does not
#: turn the agency's name into a subtype.
_HEADER_NO_FOR = re.compile(
    r"DETERMINATIONS?\s+AND\s+FINDINGS?\s+(?!(?:" + _LABEL + r"))"
    r"(.{3,100}?)(?=\s+(?:" + _LABEL + r")|\s*$)",
    re.I,
)

#: Header phrase -> canonical subtype. Order matters: the first pattern that
#: matches wins, so the specific sits above the general.
SUBTYPE_PATTERNS: list[tuple[str, str]] = [
    ("sole_source", r"sole\s*sourc|single\s*sourc"),
    ("emergency", r"emergenc"),
    ("unusual_and_compelling_urgency", r"unusual and compelling|urgenc"),
    ("non_use_of_dcss_or_set_aside", r"non-?\s*use of|dcss|set-?\s*aside|\bdslbd\b"),
    ("federal_supply_schedule",
     (r"federal supply\s*(?:\(gsa\)\s*)?(?:schedule|service)"
      r"|gsa schedule|general services administration")),
    ("cooperative_purchase", r"cooperativ|joint (?:purchas|procure)|intergovernment"),
    ("letter_contract", r"letter contract"),
    ("brand_name", r"brand\s*name"),
    ("human_care_agreement", r"human care"),
    ("competition_exemption", r"exempt\w*|other than (?:full and )?open"),
    ("competitive_sealed_proposals", r"competitive sealed proposal|\bcsp\b|request for proposal|\brfp\b"),
    ("competitive_sealed_bids", r"competitive sealed bid|invitation for bid|\bifb\b"),
    ("shortened_advertising_period", r"shorten\w*\s+(?:the\s+)?advertis"),
    ("award_other_than_low_bid", r"other than (?:the )?low(?:est)?\s*(?:priced?\s*)?bid"),
    ("rejection_of_bids", r"reject\w*\s+of\s+(?:individual\s+|all\s+)?bids?"),
    ("multiyear_contract", r"multi-?\s*year"),
    ("option_exercise", r"exercis\w*|option (?:year|period)"),
    ("price_reasonableness", r"price reasonab|reasonab\w* of (?:the )?price|fair and reasonab|\bprice\b"),
    ("contractor_responsibility", r"responsib|resposib|responsb|reponsib"),
    ("contract_modification", r"modification|change order"),
    ("termination", r"terminat"),
    ("advance_payment", r"advance payment"),
]

#: Documents whose own header is missing but whose body is a recognisable form.
#: The cooperative-purchase waiver is the common one: several agencies file it
#: as an ordinary memorandum whose subject line names the outside vehicle and
#: whose first paragraph recites the authorising code section.
BODY_FALLBACKS: list[tuple[str, str]] = [
    ("federal_supply_schedule", r"federal supply schedule|gsa (?:federal supply )?schedule"),
    ("cooperative_purchase",
     (r"2-354\.11|cooperative (?:purchas|agreement)|sourcewell|\bnaspo\b|\bomnia\b"
      r"|\bhgac\b|u\.?s\.? communities")),
    ("sole_source", r"2-354\.04\b|sole source procurement"),
    ("emergency", r"2-354\.05\b"),
    ("non_use_of_dcss_or_set_aside", r"2-354\.13\b|non-?use of dcss"),
    ("contractor_responsibility", r"prospective contractor .{0,40}responsib|responsibility of the contractor"),
    ("price_reasonableness", r"price(?:s)? (?:is|are) (?:fair and )?reasonab"),
]

#: Subtypes that record the District choosing not to run an open competition
#: for this award. Split in two because they are not the same claim.
LIMITS_COMPETITION = {
    "sole_source",
    "emergency",
    "unusual_and_compelling_urgency",
    "brand_name",
    "competition_exemption",
    "human_care_agreement",
}
#: Subtypes that record the District buying off somebody else's competition, or
#: stepping outside the DC Supply Schedule / small-business set-aside market.
#: These are weaker: a cooperative award was competed, by another jurisdiction,
#: and a set-aside waiver can mean the buy went to the open market. Counted
#: apart from LIMITS_COMPETITION and never merged into it silently.
AVOIDS_DC_MARKET = {"cooperative_purchase", "non_use_of_dcss_or_set_aside",
                    "federal_supply_schedule"}

#: Subtypes that say nothing about how the award was competed.
NEUTRAL = {
    "contractor_responsibility", "price_reasonableness", "multiyear_contract",
    "option_exercise", "contract_modification", "termination", "advance_payment",
    "competitive_sealed_proposals", "competitive_sealed_bids", "letter_contract",
    "shortened_advertising_period", "award_other_than_low_bid", "rejection_of_bids",
}


def subtype_from_text(text: str, head_chars: int = 900) -> tuple[str, str]:
    """Canonical subtype and the raw header phrase it came from.

    Returns ``("", "")`` when neither the header nor a body fallback fires.
    The header is looked for in the first ``head_chars`` characters only: these
    documents put it above everything else, and searching the whole file finds
    the phrase again inside an attached copy of a different D&F.
    """
    head = re.sub(r"\s+", " ", text[:head_chars])
    match = _HEADER.search(head) or _HEADER_NO_FOR.search(head)
    if match:
        phrase = match.group(1).strip(" .:-\u2013")
        for name, pattern in SUBTYPE_PATTERNS:
            if re.search(pattern, phrase, re.I):
                return name, phrase
        if phrase:
            return "other", phrase
    body = re.sub(r"\s+", " ", text[:4000])
    for name, pattern in BODY_FALLBACKS:
        if re.search(pattern, body, re.I):
            return name, ""
    return "", ""


def subtype_from_filename(free_text: str) -> str:
    """The same taxonomy, guessed from the filename. For comparison only."""
    name = free_text.replace(".pdf", " ")
    for subtype, pattern in SUBTYPE_PATTERNS:
        if re.search(pattern, name, re.I):
            return subtype
    return ""


# ---------------------------------------------------------------------------
# The rest of what makes a D&F useful: authority, amount, single-source claim.
# ---------------------------------------------------------------------------

#: PPRA sections, as the documents cite them: "D.C. Official Code § 2-354.04",
#: "§§ 2-354.13(8)", "2-353.01". Subsection captured where present, because
#: 2-354.13(8) means something specific and 2-354.13 alone does not.
# The dash is not always a hyphen: CW94841 prints "s 2-354.13" with an en
# dash, which an ASCII-only pattern misses entirely.
_DASH = r"[-‐-―]"
_CODE_CITE = re.compile(r"\b2" + _DASH + r"3\d{2}\.\d{2}(?:\s*\(\s*\d{1,2}\s*\))?")
_DCMR_CITE = re.compile(r"\b27\s*DCMR\b[^.;\n]{0,60}?(\d{3,4}(?:\.\d+)?)")

#: § 2-354.13(8): goods or services for the maintenance of existing software
#: where the creator of the intellectual property is the only source. A
#: countable vendor-lock-in provision, and the only subsection of 2-354.13 that
#: is a single-source claim on its face.
SOFTWARE_LOCK_IN_CITE = "2-354.13(8)"

#: Language asserting that one vendor is the only one who can do the work.
#: Checked over the whole document, because it lives in MINIMUM NEED or in the
#: findings, not in the header.
_ONLY_SOURCE = re.compile(
    r"only (?:contractor|vendor|source|firm|company|entity|supplier|manufacturer|provider|one)"
    r"|sole (?:source|provider|vendor|supplier|manufacturer)"
    r"|no other (?:vendor|contractor|firm|source|company|entity)"
    r"|proprietary (?:ownership|software|rights?|code|system|technology)"
    r"|owns? the (?:proprietary|intellectual property|source code)"
    r"|exclusive (?:rights?|distributor|reseller|provider|licensee)",
    re.I,
)

#: THE TRAP: the non-use-of-DCSS form has a checkbox reading "There was only
#: one qualified CBE to provide the service/goods", and the cooperative form
#: reports how many certified business enterprises a commodity-code search
#: returned. Both say something about the *small-business* market, not about
#: how many vendors exist, and both match :data:`_ONLY_SOURCE`. Five of the
#: first 46 documents flagged were this sentence and nothing else.
_NOT_ONLY_SOURCE = re.compile(
    r"only\s+one\s+(?:qualified\s+)?(?:SBE|CBE|certified business|small business)"
    r"|(?:resulted|identified)\s+(?:in\s+)?only\s+one",
    re.I,
)


def single_source_quote(text: str, window: int = 260) -> str:
    """The sentence asserting one available source, if the document has one.

    Three things are skipped: the small-business checkbox that
    :data:`_NOT_ONLY_SOURCE` describes; a window whose own characters are
    mojibake, which some of these forms produce in the middle of an otherwise
    readable document; and a match inside the first ``header_zone`` characters,
    which on a sole-source D&F is the title -- "DETERMINATION AND FINDINGS FOR
    A SOLE SOURCE PROCUREMENT" is not a finding, it is a heading. The heading
    is used only if the document says it nowhere else.
    """
    header_zone = 400
    fallback = ""
    for match in _ONLY_SOURCE.finditer(text):
        context = text[max(0, match.start() - 80) : match.end() + 80]
        if _NOT_ONLY_SOURCE.search(context):
            continue
        start = max(0, match.start() - window // 2)
        quote = re.sub(r"\s+", " ", text[start : match.end() + window]).strip()
        if looks_unreadable(quote):
            continue
        if match.start() < header_zone:
            fallback = fallback or quote
            continue
        return quote
    return fallback


#: A D&F that says on its face the award was not competed. Independent of the
#: header, and it has to be: CW90741's header reads "DETERMINATION AND FINDINGS
#: FOR CONTRACT AWARD" and its findings read "This acquisition is exempt from
#: competition under the Procurement Practices Reform Act (PPRA) Section
#: 2-354.13(8)". D.C. Official Code s 2-354.13 is the PPRA's list of
#: exemptions from competition; citing it, in any subsection, is the District
#: recording that it did not compete this award.
_EXEMPT_FROM_COMPETITION = re.compile(
    r"exempt(?:ion)?\s+from\s+(?:the\s+)?competition|competition\s+exempt"
    r"|2" + _DASH + r"354\.13|PPRA\s*(?:Section|Sec\.?)\s*413", re.I
)


def exempt_from_competition(text: str) -> bool:
    """Whether the document states the award was exempt from competition."""
    return bool(_EXEMPT_FROM_COMPETITION.search(text))


#: A labelled dollar figure. The D&Fs label the number they are approving --
#: "Awarded Amount:", "TOTAL AMOUNT", "not to exceed" -- and an unlabelled
#: grab of the largest "$" in the document picks up hourly rates, ceilings of
#: unrelated vehicles and page numbers.
_LABELLED_AMOUNT = re.compile(
    r"(?:award(?:ed)?\s+amount|total\s+(?:award|contract|estimated)?\s*(?:amount|value|price|cost)"
    r"|contract\s+(?:amount|value)|estimated\s+(?:amount|value|cost)|not\s+to\s+exceed"
    r"|amount\s+of\s+the\s+(?:award|contract)|price\s+of)"
    r"[^$\n]{0,60}\$\s*([\d,]+(?:\.\d{2})?)",
    re.I,
)
_ANY_AMOUNT = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")


def _money(raw: str) -> float | None:
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    # Below $1,000 these are almost always page furniture or a unit price, not
    # the value of the action being determined.
    return value if value >= 1000 else None


def citations(text: str) -> tuple[list[str], list[str]]:
    """PPRA code sections and 27 DCMR sections the document cites."""
    code, dcmr = [], []
    for match in _CODE_CITE.finditer(text):
        value = re.sub(_DASH, "-", re.sub(r"\s+", "", match.group()))
        if value not in code:
            code.append(value)
    for match in _DCMR_CITE.finditer(text):
        value = match.group(1)
        if value not in dcmr:
            dcmr.append(value)
    return code, dcmr


def stated_amount(text: str) -> tuple[float | None, str]:
    """The dollar value the D&F states, and how it was found.

    Prefers a labelled figure. Falls back to the largest dollar figure in the
    document, flagged as ``unlabelled`` so a caller can decline to use it.
    """
    labelled = [v for v in (_money(m.group(1)) for m in _LABELLED_AMOUNT.finditer(text)) if v]
    if labelled:
        return max(labelled), "labelled"
    loose = [v for v in (_money(m.group(1)) for m in _ANY_AMOUNT.finditer(text)) if v]
    if loose:
        return max(loose), "unlabelled"
    return None, ""


# ---------------------------------------------------------------------------
# Corpus enumeration
# ---------------------------------------------------------------------------

def parse_attachment_name(name: str) -> tuple[str, str, str] | None:
    """Split "CW#-<option period>-<category>-<free text>" into its three parts.

    Returns None when the name does not have all four fields. The free text is
    rejoined with "-" because it frequently contains its own hyphens.
    """
    parts = name.split("-")
    if len(parts) < 4:
        return None
    return parts[1].strip(), parts[2].strip(), "-".join(parts[3:]).strip()


def technology_contracts() -> dict[str, dict[str, str]]:
    """Technology contracts, keyed by number.

    THE TRAP: ``is_technology`` is written as the string "True"/"False" here and
    reads back from pandas as a numpy bool, where ``x is True`` is False. Read
    the CSV with :mod:`csv` and compare strings, or wrap in ``bool()``.
    """
    with open(CLASSES, newline="") as handle:
        return {r["contract_number"]: r for r in csv.DictReader(handle)
                if r["is_technology"] == "True"}


def enumerate_dfs(details: list[dict], contracts: set[str]) -> list[dict]:
    """Unique D&F attachments on the given contracts.

    ``contract_details.json`` holds one record per contract option period, and
    an agency re-uploads the same D&F under each one: CW62027 lists the same
    six files across 91 option-period records. Every upload gets its own
    document id, so ids do not deduplicate -- the file's own name does. Keeping
    (contract, filename) collapses 5,300 technology D&F attachments to 2,402
    distinct documents, and the first record in file order wins, which is the
    base period where one exists.
    """
    seen: dict[tuple[str, str], dict] = {}
    for record in details:
        number = record.get("contractNumber")
        if number not in contracts:
            continue
        for document in record.get("documents") or []:
            fields = parse_attachment_name(document["name"])
            if fields is None or fields[1] != DF_CATEGORY:
                continue
            option, _, free = fields
            key = (number, free.lower())
            if key in seen:
                continue
            seen[key] = {
                "contract_number": number,
                "option_period": option,
                "file_name": free,
                "document_id": document["id"],
                "tier": filename_tier(free),
            }
    return sorted(seen.values(), key=lambda d: (d["contract_number"], d["file_name"]))


def select_for_download(docs: list[dict], limit: int) -> list[dict]:
    """Choose which documents to spend the download budget on.

    One document per contract per tier before a second from the same contract,
    so the sample spreads across contracts rather than piling onto the few with
    thirteen D&Fs. Contracts within a tier are shuffled with a fixed seed so
    the sample is reproducible and is not ordered by contract number, which
    tracks award date.
    """
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        by_tier[doc["tier"]].append(doc)

    chosen: list[dict] = []
    for tier in (TIER_COMPETITION, TIER_UNINFORMATIVE, TIER_PRICE, TIER_RESPONSIBILITY):
        pool = by_tier.get(tier, [])
        per_contract: dict[str, list[dict]] = defaultdict(list)
        for doc in pool:
            per_contract[doc["contract_number"]].append(doc)
        order = sorted(per_contract)
        random.Random(SEED).shuffle(order)
        quota = TIER_QUOTA[tier]
        take: list[dict] = []
        depth = 0
        while any(len(per_contract[c]) > depth for c in order):
            for contract in order:
                if len(per_contract[contract]) > depth:
                    take.append(per_contract[contract][depth])
                    if quota is not None and len(take) >= quota:
                        break
            if quota is not None and len(take) >= quota:
                break
            depth += 1
        chosen.extend(take[:quota] if quota is not None else take)
        if len(chosen) >= limit:
            return chosen[:limit]
    return chosen[:limit]


# ---------------------------------------------------------------------------
# Text acquisition
# ---------------------------------------------------------------------------

STATUS_READABLE = "readable"
STATUS_SCAN = "scan_no_text_layer"
STATUS_MOJIBAKE = "mojibake"
STATUS_ERROR = "download_error"
STATUS_UNREAD = "not_downloaded"

#: D&Fs are short -- the readable ones in this corpus run 1,600 to 6,300
#: characters -- so the award-document floor of 500 is too high. A one-page
#: DCSS waiver clears 1,000. The threshold sits in an empty gap rather than at
#: a guess: across 400 downloads the largest extraction that is not a document
#: is 56 characters and the smallest that is one is over 1,000.
MIN_DF_CHARS = 300


def _text_path(document_id: str) -> Path:
    return TEXT_DIR / f"{hashlib.sha1(document_id.encode()).hexdigest()}.txt.gz"


def cached_text(document_id: str) -> str | None:
    path = _text_path(document_id)
    if not path.exists():
        return None
    return gzip.decompress(path.read_bytes()).decode("utf-8")


def fetch_text(client: DCProcurementClient, document_id: str) -> str:
    """Download one D&F and cache its text layer. "" for a scan or a failure."""
    cached = cached_text(document_id)
    if cached is not None:
        return cached
    try:
        text = extract_text(client.attachment(document_id))
    except Exception as exc:  # noqa: BLE001 - one bad PDF must not end the run
        log.warning("download/extract failed for %s: %s", document_id[:16], exc)
        text = ""
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    _text_path(document_id).write_bytes(gzip.compress(text.encode("utf-8"), 6))
    return text


def classify_status(text: str) -> str:
    if len(text.strip()) < MIN_DF_CHARS:
        return STATUS_SCAN
    if looks_unreadable(text):
        return STATUS_MOJIBAKE
    return STATUS_READABLE


# ---------------------------------------------------------------------------
# Per-document and per-contract records
# ---------------------------------------------------------------------------

DOC_COLUMNS = [
    "contract_number", "option_period", "file_name", "tier", "text_status",
    "text_chars", "readable_share", "subtype_text", "header_phrase",
    "subtype_filename", "code_citations", "dcmr_citations",
    "software_lock_in_cite", "stated_amount", "amount_basis",
    "single_source_language", "exempt_from_competition", "single_source_quote",
]

CONTRACT_COLUMNS = [
    "contract_number", "title", "vendor", "agency", "ceiling", "work_type",
    "market_type", "df_attachments", "df_unique", "df_read", "df_scanned",
    "subtypes", "limits_competition", "avoids_dc_market",
    "single_source_language", "exempt_from_competition", "software_lock_in_cite",
    "coverage",
]


def read_documents(docs: list[dict], download: bool, limit: int) -> list[dict]:
    """Fetch (or re-read from cache) each selected D&F and pull it apart."""
    rows: list[dict] = []
    client = DCProcurementClient() if download else None
    fetched = 0
    try:
        for doc in docs:
            text = cached_text(doc["document_id"])
            if text is None:
                if client is None or fetched >= limit:
                    rows.append(dict(doc, text_status=STATUS_UNREAD, text_chars=0,
                                     readable_share="", subtype_text="", header_phrase="",
                                     subtype_filename=subtype_from_filename(doc["file_name"]),
                                     code_citations="", dcmr_citations="",
                                     software_lock_in_cite=False, stated_amount="",
                                     amount_basis="", single_source_language=False,
                                     exempt_from_competition=False,
                                     single_source_quote=""))
                    continue
                text = fetch_text(client, doc["document_id"])
                fetched += 1
                if fetched % 25 == 0:
                    log.info("downloaded %d/%d", fetched, limit)
            status = classify_status(text)
            row = dict(doc)
            row["text_status"] = status
            row["text_chars"] = len(text)
            row["readable_share"] = round(readable_share(text), 3) if text.strip() else ""
            row["subtype_filename"] = subtype_from_filename(doc["file_name"])
            if status == STATUS_READABLE:
                subtype, phrase = subtype_from_text(text)
                code, dcmr = citations(text)
                amount, basis = stated_amount(text)
                quote = single_source_quote(text)
                row.update(
                    subtype_text=subtype, header_phrase=phrase,
                    code_citations=";".join(code), dcmr_citations=";".join(dcmr),
                    software_lock_in_cite=SOFTWARE_LOCK_IN_CITE in code,
                    stated_amount=amount if amount is not None else "",
                    amount_basis=basis, single_source_language=bool(quote),
                    exempt_from_competition=exempt_from_competition(text),
                    single_source_quote=quote,
                )
            else:
                # An unreadable document is unknown, not empty. Everything that
                # would be a finding stays blank/False and the status carries
                # the reason, so no downstream count can quietly treat a scan
                # as "no justification found".
                row.update(subtype_text="", header_phrase="", code_citations="",
                           dcmr_citations="", software_lock_in_cite=False,
                           stated_amount="", amount_basis="",
                           single_source_language=False,
                           exempt_from_competition=False, single_source_quote="")
            rows.append(row)
    finally:
        if client is not None:
            client.close()
    return rows


def contract_records(doc_rows: list[dict], all_docs: list[dict],
                     contracts: dict[str, dict], details: list[dict],
                     attachment_counts: Counter) -> list[dict]:
    """One competition record per technology contract that has any D&F.

    ``coverage`` is the honest field. ``read`` means at least one of the
    contract's D&Fs was downloaded and had a text layer. ``scans_only`` means
    documents were downloaded and none could be read -- the contract may well
    carry a sole-source justification and this run cannot say. ``not_sampled``
    means the download budget never reached it. Only ``read`` rows support a
    negative finding.
    """
    market: dict[str, str] = {}
    for record in details:
        number = record.get("contractNumber")
        if number and number not in market:
            market[number] = (record.get("marketType") or "").strip() or "unknown"

    unique_counts = Counter(d["contract_number"] for d in all_docs)
    by_contract: dict[str, list[dict]] = defaultdict(list)
    for row in doc_rows:
        by_contract[row["contract_number"]].append(row)

    out: list[dict] = []
    for number in sorted(unique_counts):
        meta = contracts.get(number, {})
        rows = by_contract.get(number, [])
        read = [r for r in rows if r["text_status"] == STATUS_READABLE]
        scanned = [r for r in rows if r["text_status"] in (STATUS_SCAN, STATUS_MOJIBAKE)]
        subtypes = sorted({r["subtype_text"] for r in read if r["subtype_text"]})
        if read:
            coverage = "read"
        elif scanned:
            coverage = "scans_only"
        else:
            coverage = "not_sampled"
        out.append({
            "contract_number": number,
            "title": meta.get("title", ""),
            "vendor": meta.get("vendor", ""),
            "agency": meta.get("agency", ""),
            "ceiling": meta.get("ceiling", ""),
            "work_type": meta.get("work_type", ""),
            "market_type": market.get(number, "unknown"),
            "df_attachments": attachment_counts[number],
            "df_unique": unique_counts[number],
            "df_read": len(read),
            "df_scanned": len(scanned),
            "subtypes": ";".join(subtypes),
            "limits_competition": bool(set(subtypes) & LIMITS_COMPETITION),
            "avoids_dc_market": bool(set(subtypes) & AVOIDS_DC_MARKET),
            "single_source_language": any(r["single_source_language"] for r in read),
            "exempt_from_competition": any(r["exempt_from_competition"] for r in read),
            "software_lock_in_cite": any(r["software_lock_in_cite"] for r in read),
            "coverage": coverage,
        })
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def report(doc_rows: list[dict], contract_rows: list[dict], all_docs: list[dict],
           market_all: Counter, market_tech: Counter) -> None:
    read = [r for r in doc_rows if r["text_status"] == STATUS_READABLE]
    attempted = [r for r in doc_rows if r["text_status"] != STATUS_UNREAD]
    scans = [r for r in attempted if r["text_status"] == STATUS_SCAN]
    moji = [r for r in attempted if r["text_status"] == STATUS_MOJIBAKE]

    print("=== corpus ===")
    print(f"unique technology D&Fs: {len(all_docs)} on "
          f"{len({d['contract_number'] for d in all_docs})} contracts")
    print(f"downloaded: {len(attempted)}  readable: {len(read)} ({_pct(len(read), len(attempted))})"
          f"  scans: {len(scans)} ({_pct(len(scans), len(attempted))})"
          f"  mojibake: {len(moji)}")

    print("\n=== scan rate by filename tier (downloads only) ===")
    for tier in (TIER_COMPETITION, TIER_UNINFORMATIVE, TIER_PRICE, TIER_RESPONSIBILITY):
        group = [r for r in attempted if r["tier"] == tier]
        bad = [r for r in group if r["text_status"] in (STATUS_SCAN, STATUS_MOJIBAKE)]
        print(f"  {tier:18s} n={len(group):4d}  unreadable={len(bad):4d} ({_pct(len(bad), len(group))})")

    print("\n=== scan rate by what the filename says, which is the point ===")
    print("  The documents that cannot be read are not spread evenly. They are")
    print("  concentrated on the subtype that carries the finding.")
    keywords = [
        ("sole source", r"sole\s*sourc"),
        ("exempt/competition", r"exempt|competit"),
        ("non-use / DCSS / set-aside", r"non-?use|dcss|set-?aside"),
        ("cooperative / GSA", r"cooperativ|\bcoop\b|\bgsa\b"),
        ("price reasonableness", r"price|reasonab"),
        ("responsibility", r"respons|resposib|responsb|reponsib"),
    ]
    for label, pattern in keywords:
        group = [r for r in attempted if re.search(pattern, r["file_name"], re.I)]
        bad = [r for r in group if r["text_status"] in (STATUS_SCAN, STATUS_MOJIBAKE)]
        print(f"  {label:28s} n={len(group):4d}  unreadable={len(bad):4d} "
              f"({_pct(len(bad), len(group))})")

    print("\n=== subtype, read from the document text ===")
    counts = Counter(r["subtype_text"] or "(no header found)" for r in read)
    for name, n in counts.most_common():
        tag = ("limits competition" if name in LIMITS_COMPETITION else
               "avoids DC market" if name in AVOIDS_DC_MARKET else "")
        print(f"  {n:5d}  {name:34s} {tag}")

    print("\n=== the same documents, subtype guessed from the filename ===")
    fcounts = Counter(r["subtype_filename"] or "(nothing in the name)" for r in read)
    for name, n in fcounts.most_common():
        print(f"  {n:5d}  {name}")

    agree = sum(r["subtype_text"] == r["subtype_filename"] for r in read)
    silent = sum(1 for r in read if not r["subtype_filename"] and r["subtype_text"])
    wrong = sum(1 for r in read if r["subtype_filename"] and r["subtype_text"]
                and r["subtype_filename"] != r["subtype_text"])
    nohdr = sum(1 for r in read if not r["subtype_text"])
    print(f"\ntext vs filename over {len(read)} readable documents: agree {agree}, "
          f"filename silent where the text names a subtype {silent}, "
          f"filename disagrees with the text {wrong}, text has no header {nohdr}")
    if wrong:
        print("  disagreements (filename -> text):")
        for name, n in Counter(
            f"{r['subtype_filename']} -> {r['subtype_text']}" for r in read
            if r["subtype_filename"] and r["subtype_text"]
            and r["subtype_filename"] != r["subtype_text"]
        ).most_common(12):
            print(f"    {n:4d}  {name}")

    print("\n=== marketType, against what the D&Fs say ===")
    limited = [c for c in contract_rows if c["limits_competition"]]
    avoids = [c for c in contract_rows if c["avoids_dc_market"] and not c["limits_competition"]]
    single = [c for c in contract_rows if c["single_source_language"]]
    exempt = [c for c in contract_rows if c["exempt_from_competition"]]
    either = [c for c in contract_rows
              if c["exempt_from_competition"] or c["single_source_language"]
              or c["limits_competition"]]
    for label, group in (("header subtype limits competition", limited),
                         ("header subtype avoids the DC market only", avoids),
                         ("text says exempt from competition", exempt),
                         ("text asserts a single available source", single),
                         ("any of the three", either)):
        mk = Counter(c["market_type"] for c in group)
        total = sum(float(c["ceiling"] or 0) for c in group)
        print(f"  {label}: {len(group)} contracts, ${total:,.0f} of ceiling")
        for name, n in mk.most_common():
            print(f"      marketType={name!r}: {n}")

    print("\n  The field's own vocabulary is not the limit -- it has values for this.")
    print("  marketType across every contract in the portal:")
    print("    " + ", ".join(f"{k}={v}" for k, v in market_all.most_common()))
    print("  marketType across the technology contracts:")
    print("    " + ", ".join(f"{k}={v}" for k, v in market_tech.most_common()))

    print("\n=== the uninformative-filename stratum on its own ===")
    print("  Tier B is the closest thing here to an unbiased sample: 220 documents drawn")
    print("  one per contract from the 503 whose filename names no subtype at all.")
    tier_b = [r for r in read if r["tier"] == TIER_UNINFORMATIVE]
    b_counts = Counter(r["subtype_text"] or "(no header found)" for r in tier_b)
    for name, n in b_counts.most_common():
        tag = ("limits competition" if name in LIMITS_COMPETITION else
               "avoids DC market" if name in AVOIDS_DC_MARKET else "")
        print(f"    {n:4d}  {name:34s} {tag}")

    lock = [c for c in contract_rows if c["software_lock_in_cite"]]
    print(f"\n  cite PPRA {SOFTWARE_LOCK_IN_CITE} (maintenance of existing software, "
          f"IP creator the only source): {len(lock)} contracts")
    for c in lock:
        print(f"      {c['contract_number']}  {c['vendor']}  ${float(c['ceiling'] or 0):,.0f}  "
              f"marketType={c['market_type']}")

    print("\n=== coverage ===")
    cov = Counter(c["coverage"] for c in contract_rows)
    for name, n in cov.most_common():
        print(f"  {name}: {n} contracts")
    print("  A 'scans_only' or 'not_sampled' contract is unknown, not clean.")

    read_contracts = [c for c in contract_rows if c["coverage"] == "read"]
    flagged = [c for c in read_contracts
               if c["limits_competition"] or c["single_source_language"]
               or c["exempt_from_competition"]]
    print(f"\n  Of the {len(read_contracts)} contracts where at least one D&F could be read, "
          f"{len(flagged)} ({_pct(len(flagged), len(read_contracts))}) carry a document "
          f"that limits competition, states an exemption from it, or asserts a single "
          f"available source. That rate does not generalise to the other "
          f"{len(contract_rows) - len(read_contracts)} contracts: the download budget was "
          f"deliberately spent on the documents whose filenames looked most likely to "
          f"say something about competition.")

    remaining = len(all_docs) - len(attempted)
    print(f"\nFull coverage would need {remaining} more downloads "
          f"({len(all_docs)} unique documents in total). At the measured scan rate "
          f"of {_pct(len(scans) + len(moji), len(attempted))}, roughly "
          f"{round((len(scans) + len(moji)) / max(len(attempted), 1) * len(all_docs))} "
          f"of them would need OCR to be read at all.")


def publishable_quotes(doc_rows: list[dict], contracts: dict[str, dict], top: int = 15) -> list[dict]:
    """Single-source assertions, one per contract, biggest contract first."""
    out = []
    seen: set[str] = set()
    for row in doc_rows:
        if not row["single_source_quote"] or row["contract_number"] in seen:
            continue
        seen.add(row["contract_number"])
        meta = contracts.get(row["contract_number"], {})
        out.append({
            "contract_number": row["contract_number"],
            "vendor": meta.get("vendor", ""),
            "title": meta.get("title", ""),
            "ceiling": float(meta.get("ceiling") or 0),
            "subtype": row["subtype_text"],
            "code_citations": row["code_citations"],
            "stated_amount": row["stated_amount"],
            "file_name": row["file_name"],
            "quote": row["single_source_quote"],
        })
    out.sort(key=lambda r: -r["ceiling"])
    return out[:top]


# ---------------------------------------------------------------------------
# The Task Orders category, checked cheaply
# ---------------------------------------------------------------------------

def task_order_peek(details: list[dict], contracts: dict[str, dict], sample: int) -> None:
    """Open a few "Task Orders" attachments and say what is in them.

    The briefing states that the portal has no record type for task orders
    issued under ordering vehicles, and that this is the largest gap in the
    data. The portal does carry a Task Orders *attachment* category -- 4,827
    attachments on 231 contracts -- which has never been opened, so the claim
    is worth testing against the documents rather than asserted.
    """
    rows: dict[tuple[str, str], dict] = {}
    for record in details:
        number = record.get("contractNumber")
        for document in record.get("documents") or []:
            fields = parse_attachment_name(document["name"])
            if fields is None or fields[1] != TASK_ORDER_CATEGORY:
                continue
            rows.setdefault((number, fields[2].lower()),
                            {"contract_number": number, "file_name": fields[2],
                             "document_id": document["id"]})
    docs = sorted(rows.values(), key=lambda d: (d["contract_number"], d["file_name"]))
    tech = [d for d in docs if d["contract_number"] in contracts]
    with_docs = {d["contract_number"] for d in tech}
    print(f"Task Orders: {len(docs)} unique documents on "
          f"{len({d['contract_number'] for d in docs})} contracts; "
          f"{len(tech)} on {len(with_docs)} technology contracts")

    # How much of the ordering-vehicle population that gap covers. "Indefinite"
    # in contractType is the portal's own word for an ordering vehicle; it is a
    # loose proxy and is reported as one.
    vehicles = set()
    for record in details:
        number = record.get("contractNumber")
        if number in contracts and "indefinite" in (record.get("contractType") or "").lower():
            vehicles.add(number)
    print(f"technology contracts whose contractType says Indefinite Delivery: {len(vehicles)}; "
          f"of those, {len(vehicles & with_docs)} have any Task Orders attachment")

    picks = tech[:sample] if tech else docs[:sample]
    client = DCProcurementClient()
    try:
        for doc in picks:
            text = cached_text(doc["document_id"])
            if text is None:
                text = fetch_text(client, doc["document_id"])
            amount, basis = stated_amount(text)
            others = other_contracts_named(text, doc["contract_number"])
            print("-" * 78)
            print(f"{doc['contract_number']} | {doc['file_name']}")
            print(f"  status={classify_status(text)} chars={len(text)} "
                  f"amount={amount} ({basis}) other contract numbers named={others[:4]}")
            print("  " + re.sub(r"\s+", " ", text[:400]))
    finally:
        client.close()


# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="maximum documents to download this run")
    parser.add_argument("--enumerate", action="store_true",
                        help="count the corpus and stop; no network")
    parser.add_argument("--report", action="store_true",
                        help="re-read the existing cache; no network")
    parser.add_argument("--task-orders", type=int, default=0, metavar="N",
                        help="also open N Task Orders attachments")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    details = json.load(open(DETAILS))
    contracts = technology_contracts()
    all_docs = enumerate_dfs(details, set(contracts))

    attachment_counts = Counter()
    for record in details:
        number = record.get("contractNumber")
        if number not in contracts:
            continue
        for document in record.get("documents") or []:
            fields = parse_attachment_name(document["name"])
            if fields and fields[1] == DF_CATEGORY:
                attachment_counts[number] += 1

    if args.enumerate:
        print(f"technology contracts: {len(contracts)}")
        print(f"D&F attachments on them: {sum(attachment_counts.values())}")
        print(f"unique after collapsing option-period re-uploads: {len(all_docs)} "
              f"on {len({d['contract_number'] for d in all_docs})} contracts")
        print("by filename tier:", dict(Counter(d["tier"] for d in all_docs)))
        print("selection under the cap:",
              dict(Counter(d["tier"] for d in select_for_download(all_docs, args.limit))))
        return

    selected = select_for_download(all_docs, args.limit)
    doc_rows = read_documents(selected, download=not args.report, limit=args.limit)
    contract_rows = contract_records(doc_rows, all_docs, contracts, details, attachment_counts)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(DOC_CSV, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DOC_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(doc_rows)
    with open(CONTRACT_CSV, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONTRACT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(contract_rows)
    quotes = publishable_quotes(doc_rows, contracts)
    json.dump(quotes, open(QUOTES_JSON, "w"), indent=1)

    market_all: Counter = Counter()
    market_tech: Counter = Counter()
    seen_market: set[str] = set()
    for record in details:
        number = record.get("contractNumber")
        if not number or number in seen_market:
            continue
        seen_market.add(number)
        value = (record.get("marketType") or "").strip() or "unknown"
        market_all[value] += 1
        if number in contracts:
            market_tech[value] += 1

    report(doc_rows, contract_rows, all_docs, market_all, market_tech)
    print(f"\nwrote {DOC_CSV.relative_to(ROOT)}, {CONTRACT_CSV.relative_to(ROOT)}, "
          f"{QUOTES_JSON.relative_to(ROOT)}")

    if args.task_orders:
        print("\n=== Task Orders ===")
        task_order_peek(details, contracts, args.task_orders)


if __name__ == "__main__":
    main()
