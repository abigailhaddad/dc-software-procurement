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
import json
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
    """The human-readable report: the counts, the figures that moved, then the
    README sentences that no longer match."""
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
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pulled_on": pulled_on,
        "checked": len(claims),
        "moved": moved,
        "held": len(claims) - moved,
        "beyond_tolerance": sum(1 for c in claims if c["moved"] and not c["ok"]),
        "error": error,
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
