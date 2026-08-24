#!/usr/bin/env python3
"""OCR the award PDFs that have no readable text layer.

Delivery structure could be read for 288 of 476 candidate contracts
(``data/delivery_structure.csv``). The rest split two ways: some candidates
have no award document on the portal at all, and some have a document whose
extracted text is under :data:`MIN_TEXT_CHARS` -- scanned awards. This script
handles the second group: it re-downloads each scanned PDF, rasterises the
pages that carry ink but no usable text (the heuristic from the
generic-comment-analyzer's ``attachment_utils.py``), OCRs them with
gpt-5.4-mini through LiteLLM, and caches the result in
``data/pdftext_ocr/<digest>.txt.gz`` -- the same naming scheme as
``data/pdftext/`` but a separate directory, so nothing published is touched.

It then runs the existing delivery classifier (``delivery_config.yaml``,
mirroring ``analysis/classify_delivery.py`` including the agile context
windows) over the newly readable contracts and writes
``data/delivery_structure_ocr.csv`` -- a SEPARATE file with the same columns
as ``data/delivery_structure.csv``. Nothing is integrated; the report at the
end says what the headline numbers would become if it were.

    uv run python analysis/ocr_awards.py              # full run
    uv run python analysis/ocr_awards.py --limit 3    # try a few docs first
    uv run python analysis/ocr_awards.py --enumerate  # counts only, no network
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import logging
import random
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure.classify import DEFAULT_MODEL, _load_dotenv, classify_contracts  # noqa: E402
from dcprocure.client import DCProcurementClient  # noqa: E402
from dcprocure.documents import (  # noqa: E402
    MIN_TEXT_CHARS,
    _digest,
    _text_path,
    agile_context_windows,
    agile_terms_used,
    delivery_excerpt,
    pick_award_document,
)

log = logging.getLogger("ocr_awards")

OCR_TEXT_DIR = ROOT / "data" / "pdftext_ocr"
OUT_PATH = ROOT / "data" / "delivery_structure_ocr.csv"
DELIVERY_CONFIG = ROOT / "delivery_config.yaml"
PUBLISHED = ROOT / "data" / "delivery_structure.csv"

OCR_MODEL = "gpt-5.4-mini"
MAX_OCR_PAGES = 20  # same cap as the reference implementation
OCR_DPI = 200  # enough for body text, keeps each PNG small

# Same candidate set as analysis/classify_delivery.py.
BUILD_WORK_TYPES = {
    "custom_software_build",
    "cots_implementation",
    "software_om_support",
    "it_consulting_advisory",
}

# Columns of data/delivery_structure.csv, reproduced exactly.
OUT_COLUMNS = [
    "contract_number", "regex_agile_terms", "regex_strong_terms", "title",
    "vendor", "work_type", "ceiling", "delivery_structure",
    "agile_mention_kind", "uses_agile_vocabulary", "agile_quote",
    "scope_fixed_in_advance", "deliverables_quote", "confidence",
]

# Running usage across all OCR calls, for the cost report.
USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "calls": 0}


# ---------------------------------------------------------------------------
# OCR machinery, following generic-comment-analyzer/attachment_utils.py.
# ---------------------------------------------------------------------------

def is_gibberish(text: str, min_chars: int = 10, max_control: float = 0.02,
                 min_printable: float = 0.80, min_alpha: float = 0.10) -> bool:
    """True when text looks like a failed extraction rather than content.

    Character-class test, not vocabulary: a PDF whose fonts carry no usable
    cmap yields control bytes, which real prose never does. Copied from the
    generic-comment-analyzer's attachment_utils.
    """
    stripped = text.strip() if text else ""
    if len(stripped) < min_chars:
        return True
    n = len(text)
    control = sum(1 for c in text if (ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127)
    if control / n > max_control:
        return True
    printable = sum(1 for c in text if c.isprintable() or c in "\t\n\r")
    if printable / n < min_printable:
        return True
    alpha = sum(1 for c in text if c.isalpha())
    if alpha / n < min_alpha:
        return True
    return False


def _pages_needing_ocr(doc, min_chars: int = 100) -> list[int]:
    """Page indexes with visible content but no usable text.

    The test is "has ink but no readable text", not "has a big image": it
    catches image-only scans, text drawn as vector outlines, and broken font
    cmaps alike, while skipping genuinely blank pages.
    """
    out = []
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        if len(text) >= min_chars and not is_gibberish(text):
            continue
        if page.get_image_info() or page.get_drawings():
            out.append(i)
    return out


def _ocr_page_image(png_bytes: bytes) -> str:
    """OCR one rasterised page via gpt-5.4-mini. Returns "" on failure/empty."""
    import litellm

    litellm.drop_params = True
    litellm.suppress_debug_info = True

    b64 = base64.b64encode(png_bytes).decode("utf-8")
    resp = litellm.completion(
        model=OCR_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": (
                "Extract all text from this document. Return only the raw text "
                "content. If there is no readable text, return exactly the word "
                "EMPTY and nothing else.")},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        temperature=0.0,
        timeout=180,
    )
    usage = getattr(resp, "usage", None)
    if usage is not None:
        USAGE["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
        USAGE["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
    USAGE["calls"] += 1
    try:
        import litellm as _l
        USAGE["cost_usd"] += _l.completion_cost(completion_response=resp) or 0.0
    except Exception:
        pass  # model may not be in litellm's price map; tokens still reported

    text = (resp.choices[0].message.content or "").strip()
    if not text or text.upper() == "EMPTY":
        return ""
    if is_gibberish(text):
        log.warning("  OCR returned gibberish, discarding page")
        return ""
    return text


def ocr_pdf_bytes(pdf_bytes: bytes, label: str) -> str:
    """OCR the scan-like pages of one PDF, capped at MAX_OCR_PAGES."""
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        targets = _pages_needing_ocr(doc)
        if not targets:
            log.warning("  %s: no pages flagged for OCR (%d pages)", label, doc.page_count)
            return ""
        if len(targets) > MAX_OCR_PAGES:
            log.info("  %s: %d scanned pages, OCRing the first %d",
                     label, len(targets), MAX_OCR_PAGES)
            targets = targets[:MAX_OCR_PAGES]
        else:
            log.info("  %s: OCRing %d page(s)", label, len(targets))

        chunks = []
        for i in targets:
            try:
                png = doc[i].get_pixmap(dpi=OCR_DPI).tobytes("png")
                page_text = _ocr_page_image(png)
                if page_text:
                    chunks.append(page_text)
            except Exception as exc:
                log.warning("  OCR failed on page %d of %s: %s", i + 1, label, exc)
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Candidate enumeration.
# ---------------------------------------------------------------------------

def _ocr_cache_path(document_id: str) -> Path:
    """data/pdftext_ocr/<digest>.txt.gz -- same scheme as data/pdftext/."""
    return OCR_TEXT_DIR / f"{_digest(document_id)}.txt.gz"


def enumerate_candidates() -> tuple[pd.DataFrame, list[dict], int]:
    """Split the 476 candidates into readable / unreadable-with-doc / no doc.

    Uses only the existing pdftext cache; no network. Returns (labels frame of
    unreadable-with-doc candidates as dicts, count with no document).
    """
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    candidates = labels[labels["work_type"].isin(BUILD_WORK_TYPES)]
    print(f"{len(candidates):,} candidate contracts (build/run work types)")

    details = {
        d["contractNumber"]: d
        for d in json.loads(
            (ROOT / "data" / "raw" / "contract_details.json").read_text(encoding="utf-8")
        )
    }

    unreadable: list[dict] = []
    no_document = 0
    readable = 0
    uncached = 0
    for label in candidates.itertuples():
        detail = details.get(label.contract_number)
        document = pick_award_document(detail) if detail is not None else None
        if document is None:
            no_document += 1
            continue
        cached = _text_path(document["id"])
        if not cached.exists():
            # Never seen by the pipeline; treat as unreadable so it gets a look.
            uncached += 1
            text = ""
        else:
            text = gzip.decompress(cached.read_bytes()).decode("utf-8")
        if len(text) >= MIN_TEXT_CHARS and delivery_excerpt(text):
            readable += 1
            continue
        unreadable.append({
            "contract_number": label.contract_number,
            "document_id": document["id"],
            "title": label.title,
            "vendor": label.vendor,
            "work_type": label.work_type,
            "ceiling": label.ceiling,
        })

    print(f"  {readable:,} already readable (text layer present)")
    print(f"  {len(unreadable):,} have an award document but no usable text -> OCR targets")
    if uncached:
        print(f"    (of which {uncached} had no cached extraction at all)")
    print(f"  {no_document:,} have no award document at all -> cannot be OCR'd")
    return candidates, unreadable, no_document


# ---------------------------------------------------------------------------
# Main pipeline.
# ---------------------------------------------------------------------------

def run_ocr(targets: list[dict], limit: int | None = None) -> dict[str, str]:
    """Download and OCR each target, caching results. Returns docid -> text."""
    OCR_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    todo = targets[:limit] if limit else targets

    with DCProcurementClient() as client:
        for n, target in enumerate(todo, 1):
            doc_id = target["document_id"]
            cache = _ocr_cache_path(doc_id)
            if cache.exists():
                results[doc_id] = gzip.decompress(cache.read_bytes()).decode("utf-8")
                continue
            label = f"{target['contract_number']} ({n}/{len(todo)})"
            log.info("%s: downloading", label)
            try:
                pdf_bytes = client.attachment(doc_id)
            except Exception as exc:
                log.warning("%s: download failed: %s", label, exc)
                continue
            try:
                text = ocr_pdf_bytes(pdf_bytes, label)
            except Exception as exc:
                log.warning("%s: OCR failed: %s", label, exc)
                continue
            cache.write_bytes(gzip.compress(text.encode("utf-8"), compresslevel=6))
            results[doc_id] = text
            time.sleep(0.2)  # be gentle with the portal between downloads
    return results


def build_rows(targets: list[dict], ocr_texts: dict[str, str]) -> tuple[pd.DataFrame, int, int]:
    """Rows for the newly usable contracts, plus (ocr_ok, still_unusable)."""
    rows = []
    ocr_ok = 0
    unusable = 0
    for target in targets:
        text = ocr_texts.get(target["document_id"], "")
        if not text.strip():
            continue  # download/OCR failure or empty; counted by caller
        ocr_ok += 1
        excerpt = delivery_excerpt(text)
        if not excerpt:
            unusable += 1
            continue
        agile_context = agile_context_windows(text)
        if agile_context:
            excerpt = f"{excerpt}\n\n=== passages mentioning agile terms ===\n{agile_context}"
        rows.append({
            "contract_number": target["contract_number"],
            "excerpt": excerpt,
            "regex_agile_terms": ",".join(agile_terms_used(text)),
            "regex_strong_terms": ",".join(agile_terms_used(text, strong_only=True)),
            "title": target["title"],
            "vendor": target["vendor"],
            "work_type": target["work_type"],
            "ceiling": target["ceiling"],
        })
    return pd.DataFrame(rows), ocr_ok, unusable


def spot_check(ocr_texts: dict[str, str], k: int = 5) -> None:
    """Print 300-char excerpts from k random OCR'd docs for a human to judge."""
    usable = {d: t for d, t in ocr_texts.items() if t.strip()}
    if not usable:
        print("\nSpot check: no OCR'd text to sample")
        return
    random.seed(42)
    sample = random.sample(sorted(usable), min(k, len(usable)))
    print(f"\n=== Spot check: {len(sample)} random OCR'd documents ===")
    for doc_id in sample:
        text = " ".join(usable[doc_id].split())
        print(f"\n[{_digest(doc_id)[:12]}] {text[:300]}")


def would_be_headlines(new_frame: pd.DataFrame, readable_before: int) -> None:
    """What the published numbers would become if the OCR results merged in."""
    published = pd.read_csv(PUBLISHED)
    combined = pd.concat([published, new_frame[OUT_COLUMNS]], ignore_index=True)
    combined = combined.drop_duplicates(subset="contract_number", keep="first")

    print("\n=== Would-be combined headline numbers (NOT integrated) ===")
    print(f"readable: {readable_before} -> {len(combined)} of 476 candidates")
    print("\ndelivery_structure value counts (combined):")
    for value, count in combined["delivery_structure"].value_counts().items():
        was = (published["delivery_structure"] == value).sum()
        print(f"  {value}: {was} -> {count}")
    seq = combined[combined["delivery_structure"] == "sequential_gated_deliverables"]
    seq_fixed = (seq["scope_fixed_in_advance"] == "yes").sum()
    print(f"\nsequential, gated contracts: 82 -> {len(seq)}")
    print(f"scope fixed in advance among them: 74 of 82 -> {seq_fixed} of {len(seq)}")


def apply_overrides(frame: pd.DataFrame) -> pd.DataFrame:
    """Same hand-read corrections as classify_delivery.py, for parity."""
    import yaml

    overrides = yaml.safe_load((ROOT / "delivery_overrides.yaml").read_text(encoding="utf-8"))
    for contract_number, spec in (overrides.get("agile_mention_kind") or {}).items():
        mask = frame.contract_number == contract_number
        if mask.any():
            frame.loc[mask, "agile_mention_kind"] = spec["value"]
            print(f"override {contract_number}: agile_mention_kind -> {spec['value']}")
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="OCR at most this many documents (for a trial run)")
    parser.add_argument("--enumerate", action="store_true",
                        help="only report the candidate split; no network, no OCR")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="classifier model (OCR always uses %s)" % OCR_MODEL)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv()

    candidates, targets, no_document = enumerate_candidates()
    if args.enumerate:
        return 0
    if not targets:
        print("nothing to OCR")
        return 0

    ocr_texts = run_ocr(targets, limit=args.limit)
    attempted = targets[:args.limit] if args.limit else targets
    failed = [t for t in attempted if t["document_id"] not in ocr_texts]
    empty = [t for t in attempted
             if not ocr_texts.get(t["document_id"], "").strip()
             and t["document_id"] in ocr_texts]

    frame, ocr_ok, no_excerpt = build_rows(attempted, ocr_texts)
    print(f"\n=== OCR results ===")
    print(f"attempted: {len(attempted)}")
    print(f"download/open failures: {len(failed)}")
    print(f"OCR produced no text: {len(empty)}")
    print(f"OCR produced text: {ocr_ok}")
    print(f"  usable delivery excerpt found: {len(frame)}")
    print(f"  still unusable (no excerpt): {no_excerpt}")
    if failed:
        print("failed contracts:", ", ".join(t["contract_number"] for t in failed))
    if empty:
        print("empty-OCR contracts:", ", ".join(t["contract_number"] for t in empty))

    spot_check(ocr_texts)

    if frame.empty:
        print("\nno newly usable contracts; nothing to classify")
        return 0

    records = frame[["title", "vendor", "excerpt"]].to_dict("records")
    predictions = classify_contracts(records, model=args.model, config_path=DELIVERY_CONFIG)
    for field in ("delivery_structure", "agile_mention_kind", "uses_agile_vocabulary",
                  "agile_quote", "scope_fixed_in_advance", "deliverables_quote",
                  "confidence"):
        frame[field] = [p.get(field) if p else None for p in predictions]
    frame = apply_overrides(frame)

    frame[OUT_COLUMNS].to_csv(OUT_PATH, index=False)
    print(f"\nwrote {OUT_PATH} ({len(frame):,} rows)")

    print("\nnew delivery_structure value counts:")
    print(frame["delivery_structure"].value_counts().to_string())

    readable_before = len(pd.read_csv(PUBLISHED))
    would_be_headlines(frame, readable_before)

    print(f"\n=== OCR spend ===")
    print(f"vision calls: {USAGE['calls']} "
          f"(prompt {USAGE['prompt_tokens']:,} tok, completion {USAGE['completion_tokens']:,} tok)")
    if USAGE["cost_usd"]:
        print(f"litellm-reported cost: ${USAGE['cost_usd']:.2f}")
    else:
        print("litellm did not report a cost for this model; see token counts above")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
