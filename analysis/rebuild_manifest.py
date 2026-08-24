#!/usr/bin/env python3
"""Reconstruct data/raw/manifest.json from the files actually on disk.

The pull is resumable, and a resumed or partial pull can leave the manifest
describing an earlier, smaller snapshot than the files it sits next to. That
happened with this snapshot: the manifest recorded a 14-class technology pull
while the files on disk were the later 163-class full pull. The provenance
claim is only worth anything if the manifest describes the bytes on disk, so
this rebuilds it from them directly.

    uv run python analysis/rebuild_manifest.py
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def file_stamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def main() -> int:
    snapshots = []

    contracts = json.loads((RAW / "contracts_by_commodity.json").read_text(encoding="utf-8"))
    classes = Counter()
    for row in contracts:
        for code in row.get("foundUnderClasses") or [row.get("foundUnderClass")]:
            if code:
                classes[str(code)] += 1
    snapshots.append({
        "name": "contracts_by_commodity",
        "pulled_at": file_stamp(RAW / "contracts_by_commodity.json"),
        "rows": len(contracts),
        "distinct_classes": len(classes),
        "classes": dict(sorted(classes.items())),
    })

    for name in ("contract_details", "purchase_by_commodity", "solicitations", "payments"):
        path = RAW / f"{name}.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        snapshots.append({
            "name": name,
            "pulled_at": file_stamp(path),
            "rows": len(rows),
        })

    for name in ("commodities", "agencies"):
        path = RAW / f"{name}.json"
        snapshots.append({
            "name": name,
            "pulled_at": file_stamp(path),
            "rows": len(json.loads(path.read_text(encoding="utf-8"))),
        })

    stamps = [s["pulled_at"] for s in snapshots]
    manifest = {
        "pulled_on": max(stamps)[:10],
        "pull_window": {"first_file": min(stamps), "last_file": max(stamps)},
        "source": "https://contracts.ocp.dc.gov",
        "note": (
            "Rebuilt from the files on disk by analysis/rebuild_manifest.py on "
            f"{date.today().isoformat()}. Timestamps are file modification times."
        ),
        "snapshots": snapshots,
    }
    out = RAW / "manifest.json"
    out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"wrote {out}")
    for s in snapshots:
        print(f"  {s['name']:24s} {s['rows']:>9,} rows  {s['pulled_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
