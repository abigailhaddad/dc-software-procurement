"""Command line entry point: ``dcprocure pull``, ``dcprocure pull-payments``,
``dcprocure recover-delisted``, ``dcprocure publish`` and ``dcprocure report``."""

from __future__ import annotations

import argparse
import io
import json
import logging
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from . import analysis, parse
from .archive import recover_delisted, restore_from_snapshot
from .client import DCProcurementClient
from .pull import DATA_DIR, RAW_DIR, pull_all, pull_payments, update_manifest_entry

# Last commit before the portal dropped FY2021-era contracts on 1 October 2026.
RECOVERY_BASELINE = "248abc1"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _report() -> None:
    """Print the headline figures from whatever snapshot is on disk.

    Same numbers as the notebook, so a discrepancy between
    them shows up here rather than after publication.
    """
    pd.set_option("display.width", 200)
    labels_path = DATA_DIR / "contract_classifications.csv"
    if not labels_path.exists():
        print("No classifications yet. Run: uv run python analysis/classify_contracts.py")
        return

    labels = pd.read_csv(labels_path)
    payments = parse.load_payments()
    technology = labels[labels.is_technology == True]  # noqa: E712

    print("== What the District is buying ==")
    table = labels.groupby("work_type").agg(
        contracts=("contract_number", "size"), ceiling=("ceiling", "sum")
    )
    table["ceiling_M"] = (table.ceiling / 1e6).round(0)
    print(table[["contracts", "ceiling_M"]].sort_values("ceiling_M", ascending=False).to_string())
    print(f"\n{len(technology):,} technology contracts, ${technology.ceiling.sum() / 1e6:,.0f}M in ceilings")

    print("\n== Who gets paid, FY2021-FY2025 ==")
    vendors = analysis.vendor_concentration(labels, payments)
    print(vendors.head(10).to_string())

    print(f"\n{analysis.excluded_adjacent_note(parse.load_purchase_orders())}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dcprocure", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    pull = sub.add_parser("pull", help="fetch raw data from the portal into data/raw/")
    pull.add_argument(
        "--all-classes",
        action="store_true",
        help="pull every NIGP class, not only the technology ones",
    )
    sub.add_parser(
        "pull-payments",
        help="re-fetch only the payments table (~280 MB in one response, several minutes)",
    )
    recover = sub.add_parser(
        "recover-delisted",
        help="put back contracts and periods the portal dropped on 1 October 2026",
    )
    recover.add_argument(
        "--from-snapshot",
        type=Path,
        help="an earlier data/raw (e.g. raw/ from the Hugging Face dataset) to restore "
        "from exactly, after re-fetching by id",
    )
    sub.add_parser("publish", help="upload the raw snapshot to the Hugging Face dataset")
    sub.add_parser("hydrate", help="download any missing data/raw file from the Hugging Face archive")
    sub.add_parser(
        "prune", help="delete data/raw files, only if each matches the Hugging Face archive"
    )
    sub.add_parser("report", help="print headline figures from the local snapshot")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.command == "pull":
        for snapshot in pull_all(all_classes=args.all_classes):
            print(f"{snapshot.name}: {snapshot.rows:,} rows")
    elif args.command == "pull-payments":
        # Payments is by far the largest and slowest pull, and it is the one
        # that changes weekly, so it is worth refreshing without re-pulling
        # everything else.
        with DCProcurementClient() as client:
            snapshot = pull_payments(client)
        update_manifest_entry(snapshot)
        print(f"{snapshot.name}: {snapshot.rows:,} rows")
    elif args.command == "recover-delisted":
        # What the analysis had before the portal's 1 October 2026 drop: the
        # classifications as committed in the last commit before it, which
        # record each contract's period count and summed ceiling. Pinned, so
        # committing a later classification table does not move the target.
        committed = subprocess.run(
            ["git", "show", f"{RECOVERY_BASELINE}:data/contract_classifications.csv"],
            capture_output=True, text=True, check=True, cwd=DATA_DIR.parent,
        ).stdout
        labels = pd.read_csv(io.StringIO(committed), dtype={"contract_number": str})
        expected = {
            row.contract_number: (int(row.option_periods), float(row.ceiling))
            for row in labels.itertuples()
        }
        details = json.loads((RAW_DIR / "contract_details.json").read_text(encoding="utf-8"))
        periods_on_disk = Counter(d["contractNumber"] for d in details)
        # Every contract holding fewer periods than it did: whole contracts
        # dropped (zero on disk), and listed ones whose oldest periods were.
        numbers = {n for n, (k, _) in expected.items() if periods_on_disk[n] < k}
        with DCProcurementClient() as client:
            result = recover_delisted(client, numbers, RAW_DIR, expected)
        print(json.dumps(result, indent=1))
        if args.from_snapshot:
            print(json.dumps(restore_from_snapshot(RAW_DIR, args.from_snapshot), indent=1))
    elif args.command == "publish":
        from .publish import publish

        print(publish())
    elif args.command in ("hydrate", "prune"):
        from . import publish as hf

        done = getattr(hf, args.command)()
        print(f"{args.command}: {', '.join(done) or 'nothing to do'}")
    else:
        _report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
