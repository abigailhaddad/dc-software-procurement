#!/usr/bin/env python3
"""OCR the unreadable D&Fs and measure whether it is worth paying for.

THIS SCRIPT IS THE PRICED TRIAL THAT CAME BEFORE THE PIPELINE CHANGE, kept
because it is the evidence for the cost and yield figures the briefing quotes:
75 documents, $0.77, every one returning usable text, 99% of those returning a
subtype from the header regex. ``analysis/competition.py`` now OCRs the
unreadable D&Fs itself, in the same way, with the same cache, so this script's
answer and the pipeline's should agree. Run it to re-price the work, not to
produce a figure.

At the time it was written, ``competition.py`` deliberately did not OCR: it
counted a scan as ``scan_no_text_layer`` and refused to score it, because an
unreadable document recorded as "no justification found" would understate the
finding. The cost of that honesty was that the unreadability is concentrated
where the finding is -- of the sampled D&Fs whose filename names a sole source,
over half are image-only scans, against a handful of the cooperative-purchase
ones.

It re-downloads the
documents ``data/competition/df_documents.csv`` marked ``scan_no_text_layer``
or ``mojibake``, OCRs them with the machinery in ``analysis/ocr_awards.py``,
runs the SAME parsers ``competition.py`` uses over the recovered text, and
writes ``data/competition/df_documents_ocr.csv`` -- a separate file. Nothing
under ``data/competition/`` that ``competition.py --report`` regenerates is
touched, so the committed numbers still reproduce.

Three traps, all met here. The first and third are now handled by
``competition.pages_needing_ocr`` and ``competition.ocr_df_bytes``, which moved
into the pipeline when it started OCRing; this script calls those, so the two
cannot drift into different answers.

* Mojibake pages are not caught by ``ocr_awards._pages_needing_ocr``. That
  selector skips any page with 100+ characters that clears ``is_gibberish``,
  and a broken-cmap page yields thousands of characters of printable Latin-1
  that ``is_gibberish`` passes. Left alone, a mojibake D&F takes the
  native-text fallback and comes back as the same garbage it went in as.
  ``competition.pages_needing_ocr`` adds ``looks_unreadable`` -- the 0.85
  readable share ``competition.py`` already uses -- on top of that selector.
* OCR output goes through ``competition.classify_status``, the same length and
  readable-share guard as a text layer. An OCR result that fails it is
  recorded as still-unreadable, never as a document with no justification.
* A page cap protects the budget. D&Fs are one to three pages, but the "D&Fs"
  category also holds the occasional bundled packet, and OCR is priced per
  page. Documents that hit the cap are reported.

Text is cached in ``data/pdftext_df_ocr/<sha1 of document id>.txt.gz``, a
third directory alongside ``data/pdftext/`` and ``data/pdftext_df/``, so
nothing already published can be changed by a re-run and no page is paid for
twice.

    uv run python analysis/competition_ocr.py --enumerate    # targets, no network
    uv run python analysis/competition_ocr.py --limit 10     # priced trial
    uv run python analysis/competition_ocr.py --report       # re-read the cache
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "analysis"))

import competition as comp  # noqa: E402
import ocr_awards  # noqa: E402
from dcprocure.client import DCProcurementClient  # noqa: E402
from dcprocure.vocabulary import AVOIDS_DC_MARKET, LIMITS_COMPETITION, LOCK_IN_CITES  # noqa: E402

log = logging.getLogger("competition_ocr")

OCR_TEXT_DIR = comp.OCR_TEXT_DIR
OUT_CSV = ROOT / "data" / "competition" / "df_documents_ocr.csv"
COST_JSON = ROOT / "data" / "competition" / "ocr_trial_cost.json"

MAX_OCR_PAGES = comp.MAX_OCR_PAGES

#: Both moved into competition.py when the pipeline started OCRing. Bound here
#: so this script keeps running and keeps testing the code that is in use.
pages_needing_ocr = comp.pages_needing_ocr
ocr_df_bytes = comp.ocr_df_bytes

#: Filenames whose words say the document bears on competition. This is the
#: same vocabulary competition.py reports its scan rate over, and it is used
#: here only for ordering the spend -- never to classify anything.
PRIORITY_NAME = re.compile(
    r"sole\s*sourc|exempt|competit|non-?use|dcss|set-?aside|sbe|cbe", re.I
)


def _cache_path(document_id: str) -> Path:
    return comp._ocr_path(document_id)


def targets() -> list[dict]:
    """The unreadable sampled D&Fs, highest-value first.

    ``df_documents.csv`` does not carry the document id -- it is written from
    DOC_COLUMNS, which drops it -- so the ids come back from re-enumerating the
    corpus and joining on (contract number, filename), the same key
    ``competition.enumerate_dfs`` deduplicates on.

    The target test reads ``text_layer_status``, not ``text_status``. Once the
    pipeline OCRs, a recovered document's ``text_status`` says ``ocr_readable``
    and this script would find nothing left to price; ``text_layer_status``
    keeps what the text layer alone said, which is the population that needed
    OCR. The fallback is for a df_documents.csv written before that column
    existed.
    """
    details = json.load(open(comp.DETAILS))
    contracts = comp.technology_contracts()
    ids = {(d["contract_number"], d["file_name"]): d["document_id"]
           for d in comp.enumerate_dfs(details, set(contracts))}

    out = []
    with open(ROOT / "data" / "competition" / "df_documents.csv", newline="") as handle:
        for row in csv.DictReader(handle):
            layer = row.get("text_layer_status") or row["text_status"]
            if layer not in (comp.STATUS_SCAN, comp.STATUS_MOJIBAKE):
                continue
            key = (row["contract_number"], row["file_name"])
            if key not in ids:
                log.warning("no document id for %s / %s", *key)
                continue
            named = bool(PRIORITY_NAME.search(row["file_name"]))
            out.append({
                "contract_number": row["contract_number"],
                "option_period": row["option_period"],
                "file_name": row["file_name"],
                "tier": row["tier"],
                "prior_status": layer,
                "document_id": ids[key],
                "priority": 1 if named else (2 if row["tier"] == comp.TIER_COMPETITION else 3),
            })
    out.sort(key=lambda d: (d["priority"], d["contract_number"], d["file_name"]))
    return out


def run_ocr(todo: list[dict], download: bool) -> None:
    """OCR each target in place, filling in text/pages/cost fields.

    A cached empty result is not treated as an answer -- same rule as
    ``ocr_awards.run_ocr`` -- so a later fix to the page selector can reach a
    document that previously produced nothing.
    """
    OCR_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    client = DCProcurementClient() if download else None
    try:
        for n, target in enumerate(todo, 1):
            cache = _cache_path(target["document_id"])
            if cache.exists():
                text = gzip.decompress(cache.read_bytes()).decode("utf-8")
                if text.strip():
                    target.update(ocr_text=text, ocr_pages=0, page_count=0, cached=True)
                    continue
            if client is None:
                target.update(ocr_text="", ocr_pages=0, page_count=0, cached=False,
                              failure="not_ocred")
                continue
            label = f"{target['contract_number']} ({n}/{len(todo)})"
            before = dict(ocr_awards.USAGE)
            try:
                pdf_bytes = client.attachment(target["document_id"])
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: download failed: %s", label, exc)
                target.update(ocr_text="", ocr_pages=0, page_count=0, cached=False,
                              failure="download_error")
                continue
            try:
                text, pages, total = ocr_df_bytes(pdf_bytes, label)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: OCR failed: %s", label, exc)
                target.update(ocr_text="", ocr_pages=0, page_count=0, cached=False,
                              failure="ocr_error")
                continue
            cache.write_bytes(gzip.compress(text.encode("utf-8"), 6))
            target.update(ocr_text=text, ocr_pages=pages, page_count=total, cached=False,
                          calls=ocr_awards.USAGE["calls"] - before["calls"],
                          cost_usd=ocr_awards.USAGE["cost_usd"] - before["cost_usd"])
            log.info("%s: %d/%d pages OCRed, %d chars, $%.4f so far",
                     label, pages, total, len(text), ocr_awards.USAGE["cost_usd"])
    finally:
        if client is not None:
            client.close()


ROW_COLUMNS = [
    "contract_number", "option_period", "file_name", "tier", "prior_status",
    "priority", "page_count", "ocr_pages", "ocr_chars", "ocr_status",
    "readable_share", "subtype_text", "header_phrase", "code_citations",
    "dcmr_citations", "software_lock_in_cite", "stated_amount", "amount_basis",
    "single_source_language", "exempt_from_competition", "single_source_quote",
    "cost_usd",
]


def score(todo: list[dict]) -> list[dict]:
    """Run competition.py's own parsers over the OCR text.

    ``ocr_status`` is the guard: OCR output that fails the same length and
    readable-share test a text layer would face stays unreadable, and every
    finding field stays blank/False. That keeps a failed OCR from being read
    downstream as a document with no justification in it.
    """
    rows = []
    for target in todo:
        text = target.get("ocr_text", "")
        status = comp.classify_status(text) if text.strip() else comp.STATUS_SCAN
        row = {k: target.get(k, "") for k in
               ("contract_number", "option_period", "file_name", "tier", "prior_status",
                "priority", "page_count", "ocr_pages")}
        row.update(ocr_chars=len(text), ocr_status=status,
                   readable_share=round(comp.readable_share(text), 3) if text.strip() else "",
                   subtype_text="", header_phrase="", code_citations="", dcmr_citations="",
                   software_lock_in_cite=False, stated_amount="", amount_basis="",
                   single_source_language=False, exempt_from_competition=False,
                   single_source_quote="", cost_usd=round(target.get("cost_usd", 0.0) or 0.0, 5))
        if status == comp.STATUS_READABLE:
            subtype, phrase = comp.subtype_from_text(text)
            code, dcmr = comp.citations(text)
            amount, basis = comp.stated_amount(text)
            quote = comp.single_source_quote(text)
            row.update(subtype_text=subtype, header_phrase=phrase,
                       code_citations=";".join(code), dcmr_citations=";".join(dcmr),
                       software_lock_in_cite=any(c in code for c in LOCK_IN_CITES),
                       stated_amount=amount if amount is not None else "",
                       amount_basis=basis, single_source_language=bool(quote),
                       exempt_from_competition=comp.exempt_from_competition(text),
                       single_source_quote=quote)
        rows.append(row)
    return rows


def report(rows: list[dict], todo: list[dict]) -> None:
    attempted = [r for r in rows if r["ocr_status"] != comp.STATUS_SCAN or r["ocr_chars"]]
    tried = [r for r, t in zip(rows, todo) if "ocr_text" in t]
    usable = [r for r in tried if r["ocr_status"] == comp.STATUS_READABLE]
    pages = sum(int(r["ocr_pages"] or 0) for r in tried)
    cost = ocr_awards.USAGE["cost_usd"]
    paid = [t for t in todo if not t.get("cached") and "ocr_text" in t]

    print("\n=== cost ===")
    print(f"documents OCRed this run: {len(paid)} (plus {sum(1 for t in todo if t.get('cached'))} "
          f"from cache, free)")
    print(f"pages sent: {pages}  vision calls: {ocr_awards.USAGE['calls']}")
    print(f"tokens: prompt {ocr_awards.USAGE['prompt_tokens']:,}  "
          f"completion {ocr_awards.USAGE['completion_tokens']:,}")
    print(f"spend: ${cost:.4f}")
    if paid:
        print(f"per document: ${cost / len(paid):.4f}   per page: "
              f"${cost / max(pages, 1):.4f}")
    print("pages per document:",
          dict(Counter(int(t.get('ocr_pages') or 0) for t in paid)))

    print("\n=== yield ===")
    print(f"attempted: {len(tried)}")
    print(f"  OCR produced usable text (passes the same guard a text layer faces): "
          f"{len(usable)} ({comp._pct(len(usable), len(tried))})")
    for status in (comp.STATUS_SCAN, comp.STATUS_MOJIBAKE):
        n = sum(1 for r in tried if r["ocr_status"] == status)
        if n:
            print(f"  still {status}: {n}")
    hdr = [r for r in usable if r["subtype_text"]]
    print(f"  of the usable, a subtype header was recovered: {len(hdr)} "
          f"({comp._pct(len(hdr), len(usable))})")
    print("  subtypes recovered:",
          dict(Counter(r["subtype_text"] or "(no header)" for r in usable)))
    print(f"  yield by prior status: " + str(dict(Counter(
        (r["prior_status"], r["ocr_status"]) for r in tried))))

    print("\n=== does the finding move ===")
    flagged_docs = [r for r in usable
                    if r["subtype_text"] in LIMITS_COMPETITION
                    or r["single_source_language"] or r["exempt_from_competition"]]
    print(f"documents carrying competition-limiting language: {len(flagged_docs)} of "
          f"{len(usable)} usable")

    with open(ROOT / "data" / "competition" / "contract_competition.csv", newline="") as handle:
        contracts = {r["contract_number"]: r for r in csv.DictReader(handle)}
    baseline = {n for n, r in contracts.items()
                if r["limits_competition"] == "True" or r["single_source_language"] == "True"
                or r["exempt_from_competition"] == "True"}
    added = {r["contract_number"] for r in flagged_docs} - baseline
    base_ceiling = sum(float(contracts[n]["ceiling"] or 0) for n in baseline)
    add_ceiling = sum(float(contracts[n]["ceiling"] or 0) for n in added if n in contracts)
    print(f"published: {len(baseline)} contracts, ${base_ceiling:,.0f} of ceiling")
    print(f"newly flagged by OCR: {len(added)} contracts, ${add_ceiling:,.0f} of ceiling")
    print(f"would become: {len(baseline) + len(added)} contracts, "
          f"${base_ceiling + add_ceiling:,.0f}")
    mk = Counter(contracts[n]["market_type"] for n in added if n in contracts)
    if mk:
        print("  marketType of the newly flagged:", dict(mk))
    for n in sorted(added):
        meta = contracts.get(n, {})
        doc = next(r for r in flagged_docs if r["contract_number"] == n)
        print(f"    {n}  {meta.get('vendor','')[:34]:34s} "
              f"${float(meta.get('ceiling') or 0):,.0f}  marketType={meta.get('market_type')}  "
              f"subtype={doc['subtype_text'] or '-'}")
    cov = Counter(contracts[r["contract_number"]]["coverage"] for r in usable
                  if r["contract_number"] in contracts)
    print("  coverage of the contracts whose scans became readable:", dict(cov))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="OCR at most this many documents")
    parser.add_argument("--enumerate", action="store_true", help="list targets; no network")
    parser.add_argument("--report", action="store_true", help="re-read the cache; no network")
    parser.add_argument("--budget", type=float, default=1.20,
                        help="stop once litellm-reported spend passes this many dollars")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ocr_awards._load_dotenv()

    todo = targets()
    print(f"{len(todo)} unreadable sampled D&Fs")
    print("  by priority:", dict(Counter(t["priority"] for t in todo)))
    print("  by prior status:", dict(Counter(t["prior_status"] for t in todo)))
    if args.enumerate:
        for t in todo:
            print(f"  p{t['priority']} {t['contract_number']:10s} {t['prior_status']:18s} "
                  f"{t['file_name'][:64]}")
        return 0

    todo = todo[: args.limit] if args.limit else todo
    if args.report:
        run_ocr(todo, download=False)
    else:
        # The budget guard is checked between documents, so a single document
        # can overshoot it by its own cost. Set it below the hard cap.
        batch = []
        for target in todo:
            batch.append(target)
            run_ocr([target], download=True)
            if ocr_awards.USAGE["cost_usd"] >= args.budget:
                log.warning("budget %.2f reached after %d documents; stopping",
                            args.budget, len(batch))
                break
        todo = batch

    rows = score(todo)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    report(rows, todo)
    COST_JSON.write_text(json.dumps({
        "documents_ocred": sum(1 for t in todo if not t.get("cached") and "ocr_text" in t),
        "pages": sum(int(t.get("ocr_pages") or 0) for t in todo),
        **ocr_awards.USAGE,
    }, indent=1))
    print(f"\nwrote {OUT_CSV.relative_to(ROOT)}, {COST_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
