"""Read billing terms out of the contract PDFs the portal publishes.

The portal's ``contractType`` field is unreliable for the question "how is this
work billed?" -- roughly two thirds of software-services dollars sit under
"Indefinite Delivery Indefinite Quantity", which describes an ordering vehicle
and says nothing about pricing. The contracts themselves do say, in fairly
standardized boilerplate drawn from 27 DCMR Chapter 24:

    B.2  The District contemplates award of a firm fixed price contract in
         accordance with 27 DCMR Chapter 24.
    B.3  PRICE SCHEDULE - FIRM FIXED PRICE

This module downloads award PDFs, caches them, and pulls that language out.
Two limits are worth stating up front, and both are reported rather than hidden:
some awards are scanned images with no text layer (:data:`NEEDS_OCR`), and some
are encrypted. Neither is guessed at.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .client import DCProcurementClient
from .pull import DATA_DIR

log = logging.getLogger(__name__)

PDF_DIR = DATA_DIR / "pdf"
TEXT_DIR = DATA_DIR / "pdftext"

# Below this many extracted characters a PDF is almost certainly a scan.
MIN_TEXT_CHARS = 500

# Extraction outcomes.
NEEDS_OCR = "needs_ocr"

#: Billing models, in the vocabulary DC contracts actually use.

# A price schedule that lists labor categories with hourly rates is the
# signature of staff augmentation, whatever the contract calls itself.


def _digest(document_id: str) -> str:
    """Stable filename stem for a document id.

    Document ids are long base64 strings containing characters that are awkward
    in filenames, so they are hashed.
    """
    return hashlib.sha1(document_id.encode()).hexdigest()


def _cache_path(document_id: str) -> Path:
    """Where a PDF would be cached, if PDFs were being kept."""
    return PDF_DIR / f"{_digest(document_id)}.pdf"


def _text_path(document_id: str) -> Path:
    """Where the extracted text for a document is cached."""
    return TEXT_DIR / f"{_digest(document_id)}.txt.gz"


def download(client: DCProcurementClient, document_id: str) -> bytes:
    """Fetch one PDF's bytes, without writing them to disk.

    Prefer :func:`document_text`. PDFs are only ever read for their text and
    average around 2 MB each against roughly 20 KB of gzipped text, so the
    corpus is cached as text: 607 contracts occupy 12 MB rather than 1.1 GB.
    """
    path = _cache_path(document_id)
    if path.exists():  # honour a PDF cache from an older run, if one survives
        return path.read_bytes()
    return client.attachment(document_id)


def document_text(client: DCProcurementClient, document_id: str) -> str:
    """Text of one contract document, extracted once and cached gzipped.

    The PDF itself is never written to disk: it is fetched, read for its text,
    and discarded. A previously cached PDF is used if one happens to be present,
    so an existing PDF cache is not wasted.

    Returns "" for a document with no text layer, which is how scanned awards
    present; callers should treat that as :data:`NEEDS_OCR` rather than as an
    empty contract.
    """
    cached = _text_path(document_id)
    if cached.exists():
        return gzip.decompress(cached.read_bytes()).decode("utf-8")

    pdf = _cache_path(document_id)
    content = pdf.read_bytes() if pdf.exists() else client.attachment(document_id)
    try:
        text = extract_text(content)
    except Exception as exc:
        log.warning("could not extract %s: %s", document_id[:16], exc)
        text = ""

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(gzip.compress(text.encode("utf-8"), compresslevel=6))
    return text


#: poppler's pdftotext, when it is installed. Roughly fifty times faster than
#: pypdf on these contracts -- a full-corpus conversion that took pypdf hours
#: finished in minutes -- so it is preferred and pypdf is the fallback.
PDFTOTEXT = shutil.which("pdftotext")


def extract_text(pdf_bytes: bytes) -> str:
    """Pull the text layer from a PDF. Returns "" when there is none.

    Uses poppler's ``pdftotext`` where available and falls back to pypdf. A PDF
    with no text layer is a scan; it yields "" here rather than an error, and
    callers should treat a near-empty result as :data:`NEEDS_OCR`.
    """
    if PDFTOTEXT:
        try:
            done = subprocess.run(
                [PDFTOTEXT, "-q", "-enc", "UTF-8", "-", "-"],
                input=pdf_bytes,
                capture_output=True,
                timeout=120,
            )
            if done.returncode == 0:
                return done.stdout.decode("utf-8", errors="replace")
        except (subprocess.SubprocessError, OSError) as exc:
            log.debug("pdftotext failed, falling back to pypdf: %s", exc)

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def pick_award_document(detail: dict[str, Any]) -> dict[str, str] | None:
    """Choose the document most likely to carry the price schedule.

    The portal files attachments by kind in the document name. The executed
    award holds Section B; modifications and D&Fs usually do not.
    """
    documents = detail.get("documents") or []
    for marker in ("Contract Award", "Award", "Contract"):
        matches = [d for d in documents if marker in d["name"]]
        if matches:
            # Prefer the longest name: executed originals tend to be labelled
            # more fully than stubs.
            return max(matches, key=lambda d: len(d["name"]))
    return None


# DC contracts follow a standard section layout (DC OCP form 201). Section B
# carries the contract type and price schedule; Section F carries the period of
# performance and the deliverables schedule. Those two are where delivery
# structure is visible, and pulling them out keeps a 100,000-character contract
# inside a sensible prompt.
_SECTION_B = re.compile(r"SECTION\s+B\s*[:.\-–]?\s*(?:CONTRACT\s+TYPE|SUPPLIES)", re.I)
_SECTION_C = re.compile(r"SECTION\s+C\s*[:.\-–]?\s*(?:SPECIFICATION|WORK\s+STATEMENT|SCOPE)", re.I)
_SECTION_F = re.compile(r"SECTION\s+F\s*[:.\-–]?\s*(?:PERIOD\s+OF\s+PERFORMANCE|DELIVERAB)", re.I)
_SECTION_G = re.compile(r"SECTION\s+G\s*[:.\-–]?\s*CONTRACT\s+ADMINISTRATION", re.I)

#: Terms nobody writes by accident. A contract containing these has had agile
#: practice deliberately written into it, whatever its structure turns out to be.
AGILE_TERMS_STRONG: dict[str, str] = {
    "scrum": r"\bscrum\b",
    "sprint": r"\bsprints?\b",
    "user_story": r"\buser stor(?:y|ies)\b",
    "backlog": r"\bbacklog\b",
    "product_owner": r"\bproduct owner\b",
    "kanban": r"\bkanban\b",
    "story_points": r"\bstory points?\b",
}

#: Weaker terms. "Agile" alone is often aspirational boilerplate, and
#: "iterative", "MVP" and "DevOps" appear in contexts that have nothing to do
#: with how work is planned, so these are counted separately rather than mixed in.
AGILE_TERMS_WEAK: dict[str, str] = {
    "agile": r"\bagile\b",
    "iterative": r"\biterative\b",
    "mvp": r"\bminimum viable product\b",
    "devops": r"\bdevops\b|\bCI/CD\b",
}

# SAFe is matched case-sensitively and on its own: a case-insensitive \bSAFe\b
# matches the ordinary English word "safe", which fired on 29 of 30 documents in
# a first pass and made agile vocabulary look far more common than it is.
_SAFE_FRAMEWORK = re.compile(r"\bSAFe\b|Scaled Agile Framework")

AGILE_TERMS: dict[str, str] = {**AGILE_TERMS_STRONG, **AGILE_TERMS_WEAK}


def agile_terms_used(text: str, strong_only: bool = False) -> list[str]:
    """Which agile terms appear in the text. Auditable, no model involved.

    **This counts mentions, not commitments, and the difference is large.** Hand
    checking thirteen contracts flagged by this function found five false
    positives: three matched "Experience in Agile methodology such as Scrum,
    Kanban, or Scaled Agile Framework" inside labor-category *personnel
    qualifications*, one matched a "backlog of cases pending adjudication" at an
    administrative hearings office, and one matched "Support One Sprint" as a
    SaaS *pricing unit*. Treating those as evidence of agile delivery turned a
    null result into an apparently significant one (p=0.20 -> p=0.01).

    Use this for recall -- to find candidates worth reading -- and never as the
    basis for a claim about how a contract is run. Whether a contract commits to
    agile practice requires reading the context, which is what the delivery
    classifier is for.

    Run this over the **whole** document, not an excerpt: agile practice is
    usually described in the statement of work (Section C), while the delivery
    structure that contradicts it lives in the price and deliverables schedules
    (Sections B and F). Reading vocabulary and structure from the same passage
    is how the disagreement between them gets hidden.

    Args:
        strong_only: Restrict to terms nobody uses casually, excluding bare
            "agile" and similar aspirational language.
    """
    terms = AGILE_TERMS_STRONG if strong_only else AGILE_TERMS
    found = [name for name, pattern in terms.items() if re.search(pattern, text, re.I)]
    if _SAFE_FRAMEWORK.search(text):
        found.append("safe")
    return sorted(found)


def _span(text: str, start: re.Pattern[str], end: re.Pattern[str], limit: int) -> str:
    """Text between two section headings, capped at ``limit`` characters."""
    opening = start.search(text)
    if opening is None:
        return ""
    closing = end.search(text, opening.end())
    stop = closing.start() if closing else opening.end() + limit
    return text[opening.start() : min(stop, opening.start() + limit)]


def delivery_excerpt(text: str, limit: int = 9000) -> str:
    """Pull the contract-type, price and deliverables sections out of a contract.

    Falls back to the head of the document when the standard headings are
    absent, which happens on task orders and small purchases that do not use the
    full form. Returns "" only when there is no usable text at all.
    """
    parts = [
        _span(text, _SECTION_B, _SECTION_C, limit // 2),
        _span(text, _SECTION_F, _SECTION_G, limit // 2),
    ]
    excerpt = "\n\n...\n\n".join(p for p in parts if p)
    if excerpt.strip():
        return excerpt[:limit]
    return text[:limit] if len(text) >= MIN_TEXT_CHARS else ""


def agile_context_windows(text: str, window: int = 320, limit: int = 6) -> str:
    """Passages surrounding each agile term, so its context can be judged.

    Delivery structure lives in the price and deliverables schedules, but agile
    language usually does not: it turns up in the statement of work, and --
    crucially -- in labor-category qualification tables. An excerpt built only
    from Sections B and F therefore cannot tell a delivery commitment from a
    hiring requirement, which is precisely the distinction that matters.

    Returns "" when no agile term appears.
    """
    seen: set[int] = set()
    windows: list[str] = []
    patterns = list(AGILE_TERMS.values()) + [_SAFE_FRAMEWORK.pattern]
    for pattern in patterns:
        flags = 0 if pattern == _SAFE_FRAMEWORK.pattern else re.I
        for match in re.finditer(pattern, text, flags):
            start = max(0, match.start() - window)
            if any(abs(start - s) < window for s in seen):
                continue  # overlapping window already captured
            seen.add(start)
            windows.append(re.sub(r"\s+", " ", text[start : match.end() + window]))
            if len(windows) >= limit:
                return "\n---\n".join(windows)
    return "\n---\n".join(windows)
