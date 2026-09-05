"""Command line entry point: ``dcprocure pull``, ``dcprocure pull-payments``
and ``dcprocure report``."""

from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from . import analysis, parse
from .client import DCProcurementClient
from .pull import DATA_DIR, pull_all, pull_payments, update_manifest_entry


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _report() -> None:
    """Print the headline figures from whatever snapshot is on disk.

    Same numbers as the notebook and the briefing, so a discrepancy between
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
    else:
        _report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
