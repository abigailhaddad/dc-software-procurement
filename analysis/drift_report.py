#!/usr/bin/env python3
"""Recompute every published claim and report which ones moved.

analysis/verify_claims.py is a tripwire: it holds each published figure against
a literal and exits non-zero the moment one no longer holds. That is what you
want before publishing, and the wrong shape entirely for a job that runs every
week against a portal that changes every day. Payments alone gained roughly
three thousand rows in twelve days, so a weekly tripwire is a weekly red build
that nobody reads.

This runs the same checks and reports the movement instead. Drift is the
finding, not the failure: the exit code is zero whether nothing moved or half
the briefing did. It exits non-zero only when the run itself broke -- a missing
input file, or a genuine error in the recomputation.

    uv run python analysis/drift_report.py
    uv run python analysis/drift_report.py --out data/drift

Two files land per run, named for the snapshot they were computed from:
drift-<pulled_on>.json for anything downstream, drift-<pulled_on>.txt to read.
latest.json and latest.txt are copies of the most recent pair, so a scheduled
job has a fixed path to look at. Re-running against the same snapshot rewrites
that snapshot's files rather than piling up near-identical reports.

The published figures themselves are never touched. Deciding that 866,823
payments has become 869,839 payments, and rewriting the briefing to say so, is
a person's call; this only says which numbers are waiting for it.

Two kinds of finding come out of it. Most checks hold a published figure against
the data, and the report gives the before, the after and the movement. The checks
over README.md's prose are different in kind: each builds the sentence the current
data supports and asks whether the file still contains it, so there is no amount
by which one moved -- the sentence is either there or stale. Those are grouped at
the end of the report, each shown as the wording the README would need.

A count of contracts moving does not say why it moved, and the two reasons are
not the same event. New contract numbers in the portal are the District buying
things; an existing contract that comes back with a different work type is this
pipeline changing its mind about something it had already seen, and it moves a
published figure with nothing having happened at the portal at all. The contract
corpus section separates them, comparing data/contract_classifications.csv
against the copy at git HEAD, which is the labels as published. It leads with the
relabels and names each one. If that baseline cannot be read out of git the
section says so and the rest of the report is unaffected.

A number of checks index into a specific row -- contract CW91684's detail
record, the FY2021 payment total, a named labor category on a rate card. When
the portal drops one of those, the claim fails on its own and the run carries
on: the report shows it as <row not found: ...> rather than a zero, and every
later check still runs. That is worth a person's attention in a way ordinary
drift is not, since a contract leaving the portal is not the same event as a
number moving, but it no longer costs the rest of the report.

A run can still break outright -- a missing input file, or a genuine error in
the recomputation. Then the report is written anyway, covering the checks that
did run, with the exception recorded at the top and a non-zero exit.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

import verify_claims  # noqa: E402

DEFAULT_OUT = ROOT / "data" / "drift"
MANIFEST = ROOT / "data" / "raw" / "manifest.json"


def snapshot_date() -> str:
    """The date the data under data/raw was pulled, per its manifest.

    A drift report is only meaningful next to the snapshot it was computed
    from, so a manifest that is missing or has lost its date is a hard error
    rather than something to paper over with today's date.
    """
    if not MANIFEST.exists():
        raise FileNotFoundError(f"{MANIFEST} is missing; run: make pull")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pulled_on = manifest.get("pulled_on")
    if not pulled_on:
        raise ValueError(
            f"{MANIFEST} records no pulled_on date; "
            "rebuild it: uv run python analysis/rebuild_manifest.py"
        )
    return str(pulled_on)


def _numeric(value: object) -> float | None:
    """The value as a float, or None if it is not a figure that can move by an amount."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


README_PREFIX = "README: '"


def prose_phrase(record: dict) -> str | None:
    """The sentence a README prose check looked for, or None for an ordinary figure.

    The checks in verify_claims.py's "the README's own figures" section build a
    sentence out of the *current* data and assert that README.md still contains
    it, so both sides of the check are booleans and the figure a reader wants is
    in the claim label rather than in the values. Pulling it out here is what
    lets these be reported as a stale sentence with the wording it needs, rather
    than as True becoming False.
    """
    if not (isinstance(record["asserted"], bool) and isinstance(record["computed"], bool)):
        return None
    claim = record["claim"]
    if not (claim.startswith(README_PREFIX) and claim.endswith("'")):
        return None
    return claim[len(README_PREFIX) : -1]


def movement(record: dict) -> dict:
    """Describe one check: whether it moved, and by how much where that has meaning."""
    asserted, computed = record["asserted"], record["computed"]
    a, c = _numeric(asserted), _numeric(computed)
    moved = (a != c) if (a is not None and c is not None) else (asserted != computed)
    out = dict(record)
    out["moved"] = moved
    out["change"] = None
    out["change_pct"] = None
    # Two kinds of finding: a figure that has shifted, and a sentence in the
    # README that no longer states what the data shows. They read differently
    # and they are acted on differently, so they carry the distinction from here.
    phrase = prose_phrase(record)
    out["kind"] = "prose" if phrase is not None else "figure"
    out["phrase"] = phrase
    if a is not None and c is not None:
        out["change"] = c - a
        # A claim published as zero has no percentage to move by; the absolute
        # change is the whole story there.
        if a != 0:
            out["change_pct"] = 100.0 * (c - a) / a
    return out


def fmt(value: object) -> str:
    """Render a figure the way the checks print it, with thousands separators."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def render_text(report: dict) -> str:
    """The human-readable report: the counts, what the contract corpus did, the
    figures that moved, then the README sentences that no longer match."""
    lines = [
        "Published claim drift",
        f"data snapshot:  {report['pulled_on']}  (data/raw/manifest.json)",
        f"report written: {report['generated_at']}",
        "",
    ]
    if report["error"]:
        lines += [
            "THE RUN DID NOT FINISH. Figures below cover only the checks that ran",
            "before it stopped.",
            "",
            f"  {report['error']['type']}: {report['error']['message']}",
            "",
        ]

    moved = [c for c in report["claims"] if c["moved"]]
    beyond = [c for c in moved if not c["ok"]]
    figures = [c for c in moved if c["kind"] == "figure"]
    stale = [c for c in moved if c["kind"] == "prose"]
    lines += [
        f"{report['checked']} claims checked, {report['moved']} moved, {report['held']} held.",
    ]
    if moved:
        lines.append(
            f"Of the {len(moved)} that moved, {len(beyond)} are outside the tolerance "
            "the check allows;"
        )
        lines.append(
            f"{len(moved) - len(beyond)} moved within it. Nothing here has been "
            "rewritten in the briefing."
        )
    if stale:
        lines.append(
            f"{len(stale)} of the {len(moved)} are sentences in README.md rather than "
            "figures, listed last."
        )
    lines.append("")
    lines += render_corpus(report.get("corpus_change"))

    if not moved:
        lines += ["Every published figure still holds against this snapshot.", ""]
        return "\n".join(lines)

    if not figures:
        lines += ["No published figure moved.", ""]

    section = None
    for claim in figures:
        if claim["section"] != section:
            section = claim["section"]
            lines += [f"-- {section} --"]
        flag = "  beyond tolerance" if not claim["ok"] else ""
        change = ""
        if claim["change"] is not None:
            change = f"  {claim['change']:+,.4f}".rstrip("0").rstrip(".")
            if claim["change_pct"] is not None:
                pct = claim["change_pct"]
                # A tenth of a percent of 866,823 payments is 867 rows, so the
                # small movements are the ones that need the extra digit.
                change += f" ({pct:+.1f}%)" if abs(pct) >= 0.1 else f" ({pct:+.3f}%)"
        lines += [
            f"  {claim['claim']}",
            (
                f"      published {fmt(claim['asserted'])}"
                f"   now {fmt(claim['computed'])}{change}{flag}"
            ),
        ]
    lines.append("")

    if stale:
        lines += render_stale_prose(stale)
    return "\n".join(lines)


def render_stale_prose(stale: list[dict]) -> list[str]:
    """The README sentences that no longer match, and the wording each would need.

    These are not figures that moved by an amount, so they get no before-and-after
    pair and no percentage. What a reader needs is the sentence the current data
    supports, which is what the check built and searched for.
    """
    noun = "sentence" if len(stale) == 1 else "sentences"
    return (
        [
            "-- README.md sentences that no longer match the data --",
            "  The README restates published figures in prose. It does not contain the",
            (
                f"  {len(stale)} {noun} below, each written the way the README "
                "writes it from the"
            ),
            "  current data. Editing README.md to say them is the fix.",
            "",
        ]
        + [f"      {claim['phrase']}" for claim in stale]
        + [""]
    )


CONTRACT_LABELS = ROOT / "data" / "contract_classifications.csv"
BASELINE_REF = "HEAD"


def _labels_from_csv(text: str) -> dict[str, dict[str, str]]:
    """The label rows keyed by contract number, read as plain strings.

    Deliberately the stdlib reader rather than pandas. Everything below either
    compares two label strings or prints one, so there is nothing to gain from a
    frame, and pandas hands is_technology back as numpy.bool_, which is not the
    True that `is True` tests against -- the obvious comparison silently finds no
    technology contracts at all.
    """
    rows = {}
    for row in csv.DictReader(io.StringIO(text)):
        number = (row.get("contract_number") or "").strip()
        if number:
            rows[number] = row
    return rows


def _truthy(value: object) -> bool:
    """A CSV boolean cell as a bool. 'False' is a non-empty string, so bool() alone lies."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def baseline_labels() -> tuple[dict[str, dict[str, str]] | None, str | None]:
    """The published contract labels out of git, or None and the reason there are none.

    The comparison sits on top of the drift run rather than being part of it, so
    every way git can fail to produce that file -- no git on PATH, a shallow or
    detached checkout, the path not tracked at that ref -- degrades to a line in
    the report saying so. This job runs unattended every week and a missing
    baseline must not cost the rest of it.
    """
    rel = CONTRACT_LABELS.relative_to(ROOT).as_posix()
    try:
        proc = subprocess.run(
            ["git", "show", f"{BASELINE_REF}:{rel}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, f"could not run git ({exc})"
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        why = detail[0] if detail else "no output"
        return None, f"git show {BASELINE_REF}:{rel} failed ({why})"
    rows = _labels_from_csv(proc.stdout)
    if not rows:
        return None, f"{BASELINE_REF} holds no contract rows at {rel}"
    return rows, None


def relabel_record(number: str, was: dict, now: dict) -> dict:
    """One contract whose work type changed, with what else about it changed.

    The ceiling and the option-period count are carried because they are the
    usual reason a settled contract gets re-judged: exercising an option changes
    the summed ceiling, which invalidates the classifier's cache entry for that
    contract and sends it back for a fresh answer that need not match the old one.
    """
    return {
        "contract_number": number,
        "title": now.get("title", ""),
        "old_work_type": was.get("work_type", ""),
        "new_work_type": now.get("work_type", ""),
        "old_is_technology": _truthy(was.get("is_technology")),
        "new_is_technology": _truthy(now.get("is_technology")),
        "old_ceiling": was.get("ceiling", ""),
        "new_ceiling": now.get("ceiling", ""),
        "old_option_periods": was.get("option_periods", ""),
        "new_option_periods": now.get("option_periods", ""),
    }


def corpus_change() -> dict:
    """New contracts against relabelled ones, for the contract corpus.

    A contract count that moved says nothing on its own about why it moved. Ten
    new contract numbers in the portal and one existing contract re-judged into a
    different work type both add to a published total, and they mean opposite
    things: the first is the District buying something, the second is this
    pipeline changing its mind about something it had already seen. The report
    was showing the sum and leaving a reader to guess the split, so this computes
    it against the labels as they were published.
    """
    baseline, reason = baseline_labels()
    if baseline is None:
        return {"available": False, "unavailable_reason": reason, "baseline_ref": BASELINE_REF}
    current = _labels_from_csv(CONTRACT_LABELS.read_text(encoding="utf-8"))

    added = sorted(set(current) - set(baseline))
    dropped = sorted(set(baseline) - set(current))
    relabeled = [
        relabel_record(n, baseline[n], current[n])
        for n in sorted(set(baseline) & set(current))
        if baseline[n].get("work_type") != current[n].get("work_type")
    ]

    tech_baseline = sum(1 for r in baseline.values() if _truthy(r.get("is_technology")))
    tech_current = sum(1 for r in current.values() if _truthy(r.get("is_technology")))
    new_tech = sum(1 for n in added if _truthy(current[n].get("is_technology")))
    dropped_tech = sum(1 for n in dropped if _truthy(baseline[n].get("is_technology")))
    relabeled_tech = sum(
        (1 if r["new_is_technology"] else -1)
        for r in relabeled
        if r["new_is_technology"] != r["old_is_technology"]
    )
    return {
        "available": True,
        "unavailable_reason": None,
        "baseline_ref": BASELINE_REF,
        "baseline_rows": len(baseline),
        "current_rows": len(current),
        "new_contracts": len(added),
        "new_technology_contracts": new_tech,
        "disappeared_contracts": [
            {
                "contract_number": n,
                "title": baseline[n].get("title", ""),
                "work_type": baseline[n].get("work_type", ""),
                "is_technology": _truthy(baseline[n].get("is_technology")),
            }
            for n in dropped
        ],
        "relabeled": relabeled,
        "technology_baseline": tech_baseline,
        "technology_current": tech_current,
        # What each cause contributed, so the movement the technology-contract
        # check reports adds back up from the three of them.
        "technology_from_new": new_tech,
        "technology_from_relabel": relabeled_tech,
        "technology_from_dropped": -dropped_tech,
    }


def render_corpus(corpus: dict | None) -> list[str]:
    """The contract corpus section: what the District added against what we re-judged.

    Relabels lead. A new contract number is the portal doing its job and needs
    nothing from anyone; a relabel means a published figure moved because this
    pipeline answered its own question differently on the second pass, which is
    the one a person has to look at before the figure is rewritten.
    """
    head = ["-- contract corpus, against the published labels --"]
    if corpus is None:
        return []
    if not corpus.get("available"):
        return head + [
            f"  Not compared: {corpus.get('unavailable_reason')}.",
            "  New contracts and relabelled ones cannot be told apart in this run.",
            "",
        ]

    lines = head + [
        "  A contract count can move because the District bought something or because",
        "  this pipeline relabelled something it had already seen. Baseline:",
        f"  data/contract_classifications.csv at git {corpus['baseline_ref']}"
        f" ({corpus['baseline_rows']:,} contracts), now {corpus['current_rows']:,}.",
        "",
    ]

    relabeled = corpus["relabeled"]
    if relabeled:
        noun = "contract" if len(relabeled) == 1 else "contracts"
        lines += [
            f"  {len(relabeled)} existing {noun} changed classification. A relabel moves a "
            "published",
            "  figure without anything having happened at the portal, so it is the finding",
            "  here to check first:",
            "",
        ]
        for rec in relabeled:
            lines += render_relabel(rec)
    else:
        lines += ["  No existing contract changed classification.", ""]

    lines += [
        f"  {corpus['new_contracts']:,} contract numbers are new since the baseline, "
        f"{corpus['new_technology_contracts']:,} of them classified",
        "  as technology.",
    ]
    dropped = corpus["disappeared_contracts"]
    if dropped:
        noun = "contract" if len(dropped) == 1 else "contracts"
        lines.append(f"  {len(dropped):,} {noun} left the portal:")
        lines += [
            f"      {rec['contract_number']}  {rec['title']}"
            f"  ({rec['work_type'] or 'unlabelled'})"
            for rec in dropped
        ]
    else:
        lines.append("  No contract disappeared.")
    lines += [
        "",
        f"  Technology contracts: {corpus['technology_baseline']:,} at the baseline, "
        f"{corpus['technology_current']:,} now"
        f" ({fmt_signed(corpus['technology_from_new'])} new,"
        f" {fmt_signed(corpus['technology_from_relabel'])} relabelled,"
        f" {fmt_signed(corpus['technology_from_dropped'])} gone).",
        "",
    ]
    return lines


def fmt_signed(count: int) -> str:
    """A contributing count with its sign kept, so the three parts read as a sum."""
    return f"{count:+,}"


def render_relabel(rec: dict) -> list[str]:
    """One relabelled contract: what it is, what it was called, and what moved under it."""
    tech = ""
    if rec["old_is_technology"] != rec["new_is_technology"]:
        was = "technology" if rec["old_is_technology"] else "not technology"
        now = "technology" if rec["new_is_technology"] else "not technology"
        tech = f"   ({was} -> {now})"
    lines = [
        f"      {rec['contract_number']}  {rec['title']}",
        f"          {rec['old_work_type']} -> {rec['new_work_type']}{tech}",
    ]
    underlying = []
    if rec["old_ceiling"] != rec["new_ceiling"]:
        underlying.append(f"ceiling {rec['old_ceiling']} -> {rec['new_ceiling']}")
    if rec["old_option_periods"] != rec["new_option_periods"]:
        underlying.append(
            f"option periods {rec['old_option_periods']} -> {rec['new_option_periods']}"
        )
    if underlying:
        lines.append("          " + ", ".join(underlying))
    lines.append("")
    return lines


def build_report(pulled_on: str) -> dict:
    """Run the checks and assemble the report, finished run or not."""
    verify_claims.VERBOSE = False
    error = None
    try:
        verify_claims.run_checks()
    except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    # RESULTS holds whatever ran, which is everything unless a check raised.
    claims = [movement(r.as_dict()) for r in verify_claims.RESULTS]
    moved = sum(1 for c in claims if c["moved"])
    try:
        corpus = corpus_change()
    except (OSError, csv.Error) as exc:  # noqa: BLE001 -- reported, not swallowed
        # The comparison is an addition to the run. Losing it should cost the
        # section, not the report the rest of the checks produced.
        corpus = {
            "available": False,
            "unavailable_reason": f"{type(exc).__name__}: {exc}",
            "baseline_ref": BASELINE_REF,
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pulled_on": pulled_on,
        "checked": len(claims),
        "moved": moved,
        "held": len(claims) - moved,
        "beyond_tolerance": sum(1 for c in claims if c["moved"] and not c["ok"]),
        "error": error,
        "corpus_change": corpus,
        "claims": claims,
    }


def write_report(report: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    text = render_text(report)
    body = json.dumps(report, indent=1) + "\n"
    written = []
    for path, content in [
        (out_dir / f"drift-{report['pulled_on']}.json", body),
        (out_dir / f"drift-{report['pulled_on']}.txt", text),
        (out_dir / "latest.json", body),
        (out_dir / "latest.txt", text),
    ]:
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--out",
        metavar="DIR",
        type=Path,
        default=DEFAULT_OUT,
        help=f"directory to write the report into (default: {DEFAULT_OUT.relative_to(ROOT)})",
    )
    args = parser.parse_args(argv)

    try:
        pulled_on = snapshot_date()
    except (FileNotFoundError, ValueError) as exc:
        # A cron job's mail should say what is missing, not carry a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = build_report(pulled_on)
    written = write_report(report, args.out)

    print(render_text(report))
    for path in written:
        print(f"wrote {path}")
    if report["error"]:
        print(f"\n{report['error']['traceback']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
