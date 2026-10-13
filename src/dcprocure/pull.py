"""Pull raw data from the transparency portal onto disk.

Everything lands as raw JSON under ``data/raw`` alongside a manifest recording
when it was pulled and what was asked for. Analysis reads from disk, never from
the network, so a result can be re-derived later from the same snapshot.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .archive import retain_by_group, retain_by_key
from .client import DCProcurementClient, Filter, PortalError
from .commodities import IT_ALL

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
RAW_DIR = DATA_DIR / "raw"


@dataclass(frozen=True)
class Snapshot:
    """A pulled dataset and the provenance needed to reproduce it."""

    name: str
    pulled_at: str
    rows: int
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pulled_at": self.pulled_at,
            "rows": self.rows,
            **self.detail,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    log.info("wrote %s (%s)", path.name, f"{path.stat().st_size / 1e6:.1f} MB")


def pull_reference(client: DCProcurementClient, out_dir: Path = RAW_DIR) -> None:
    """Pull the lookup tables: commodity classes, agency clusters and the independent-agency list."""
    _write(out_dir / "commodities.json", client.commodities())
    _write(out_dir / "agencies.json", client.agencies())
    _write(out_dir / "independent_agencies.json", client.independent_agencies())


def pull_by_commodity(
    client: DCProcurementClient,
    module: str,
    classes: Iterable[str],
    out_dir: Path = RAW_DIR,
) -> Snapshot:
    """Pull one module, one NIGP class at a time, and merge the results.

    Querying class by class (rather than pulling everything and filtering) keeps
    every request to a filter the server demonstrably applies, and it records
    which class each row was found under. A contract carrying several NIGP codes
    is returned under each, so rows are de-duplicated on their portal id.
    """
    seen: dict[str, dict[str, Any]] = {}
    per_class: dict[str, int] = {}

    for class_code in classes:
        rows = client.search(module, [Filter("CommodityCode", class_code)])
        per_class[class_code] = len(rows)
        log.info("%s / NIGP %s: %d rows", module, class_code, len(rows))
        for row in rows:
            key = _row_key(module, row)
            existing = seen.setdefault(key, {**row, "matched_classes": []})
            existing["matched_classes"].append(class_code)

    path = out_dir / f"{module}_by_commodity.json"
    if module == "purchase":
        # A revised order comes back under a new number (PO736374-V4 becomes
        # -V5) and the old one stops appearing: 458 of the 460 orders that left
        # between September and October did so. Keyed on the full number, the
        # superseded version would be kept beside its replacement and counted
        # twice, so all versions of an order are one group.
        records, retained = retain_by_group(
            path, list(seen.values()), lambda r: _po_base(r["poNumber"])
        )
    else:
        records, retained = retain_by_key(
            path, list(seen.values()), lambda r: _row_key(module, r)
        )
    _write(path, records)
    return Snapshot(
        name=f"{module}_by_commodity",
        pulled_at=_now(),
        rows=len(records),
        detail={
            "classes": per_class,
            "rows_before_dedupe": sum(per_class.values()),
            "listed": len(seen),
            "retained_delisted": retained,
        },
    )


def _po_base(po_number: str) -> str:
    """The order a purchase-order number belongs to, without its -Vn revision."""
    return re.sub(r"-V\d+$", "", po_number)


def _row_key(module: str, row: dict[str, Any]) -> str:
    """Stable identity for a result row.

    Contract rows carry an opaque ``id`` that encodes contract number plus
    option period. Purchase orders have no id, so the PO number stands in.
    """
    for field_name in ("id", "poNumber", "voucherNumber"):
        if row.get(field_name):
            return str(row[field_name])
    raise PortalError(f"no identifying field on {module} row: {sorted(row)}")


def pull_contract_details(
    client: DCProcurementClient,
    contract_ids: list[str],
    out_dir: Path = RAW_DIR,
    workers: int = 6,
    refresh: bool = False,
) -> Snapshot:
    """Fetch the detail record for each contract option period.

    Detail records are the only source for ``contractType`` -- the field that
    distinguishes a fixed-price deliverable from a time-and-materials staffing
    arrangement -- and for the list of attached PDFs. One request per id, so
    this is the slow step, and it resumes: ids already on disk are kept and only
    the new ones are fetched. Pass ``refresh=True`` to re-pull everything.

    ``contract_ids`` comes from the cumulative search file, so it includes
    delisted contracts; their details stay on disk and are not re-fetched.
    """
    path = out_dir / "contract_details.json"
    existing: dict[str, dict[str, Any]] = {}
    if path.exists() and not refresh:
        existing = {d["id"]: d for d in json.loads(path.read_text(encoding="utf-8"))}
    todo = [cid for cid in contract_ids if cid not in existing]
    log.info("details: %d cached, %d to fetch", len(existing), len(todo))

    def fetch(contract_id: str) -> dict[str, Any] | None:
        try:
            return client.contract_details(contract_id)
        except PortalError as exc:
            log.warning("details failed for %s: %s", contract_id, exc)
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = [r for r in pool.map(fetch, todo) if r is not None]

    merged = {**existing, **{r["id"]: r for r in fetched}}
    records = [merged[cid] for cid in contract_ids if cid in merged]
    _write(path, records)
    return Snapshot(
        name="contract_details",
        pulled_at=_now(),
        rows=len(records),
        detail={
            "requested": len(contract_ids),
            "from_cache": len(contract_ids) - len(todo),
            "fetched": len(fetched),
            "failed": len(todo) - len(fetched),
        },
    )


def pull_solicitations(
    client: DCProcurementClient,
    fiscal_years: Iterable[str],
    out_dir: Path = RAW_DIR,
) -> Snapshot:
    """Pull open solicitations plus the archive for the given fiscal years.

    Solicitations are where the District says what it wants before anyone bids,
    so this is the RFP/RFQ text. The module carries no commodity code, and its
    ``Keyword`` filter is ignored server-side, so narrowing to technology work
    has to be done locally against the title.
    """
    records: list[dict[str, Any]] = []
    for row in client.open_solicitations():
        records.append({**row, "archive_fy": None, "is_open": True})

    per_year: dict[str, int] = {}
    for fy in fiscal_years:
        rows = client.archived_solicitations(fy)
        per_year[fy] = len(rows)
        log.info("solicitations %s: %d rows", fy, len(rows))
        for row in rows:
            records.append({**row, "archive_fy": fy, "is_open": False})

    _write(out_dir / "solicitations.json", records)
    return Snapshot(
        name="solicitations",
        pulled_at=_now(),
        rows=len(records),
        detail={"open": sum(1 for r in records if r["is_open"]), "archived": per_year},
    )


# The payments module requires at least one filter and applies none of them.
# FiscalYear, PaymentFiscalYear, CommodityCode, AgencyName and ContractNumber
# were each sent on their own and each returned the identical full table, so
# this filter is only there to satisfy the API; it narrows nothing and the
# value does not need keeping current.
PAYMENTS_FILTER = Filter("PaymentFiscalYear", "FY2026")

# Floor for a plausible payments pull. The August 2026 snapshot held 866,823
# rows and the table only grows, so anything near this number is the universe
# and anything well below it is a truncated or partial response. The gap is
# deliberately wide: this guard is not trying to detect a few thousand rows of
# drift, it is trying to catch a response that came back short.
PAYMENTS_MIN_ROWS = 800_000

# Keys every payment row carried in the published snapshot. Values are often
# blank -- 55% of rows have no contract number -- but the keys themselves are
# what parse.load_payments reads, so a row missing one means the response shape
# changed underneath us.
PAYMENTS_REQUIRED_KEYS = frozenset(
    {
        "id",
        "agencyName",
        "contractNumber",
        "vendorName",
        "voucherAmount",
        "paymentAmount",
        "paymentDate",
    }
)


def pull_payments(client: DCProcurementClient, out_dir: Path = RAW_DIR) -> Snapshot:
    """Pull the whole payments table: every voucher line the District paid.

    This is the largest input to the analysis and the basis of the vendor
    concentration figures, and it is the one module that cannot be narrowed. The
    payments search ignores every filter it is given -- FiscalYear,
    PaymentFiscalYear, CommodityCode, AgencyName and ContractNumber were each
    sent alone and each returned the same complete table -- so there is no slice
    to ask for and no way to pull it in pieces. It comes back unpaginated in one
    response of roughly 280 MB, near 300 MB once written out, which is why
    :class:`DCProcurementClient` defaults to a 300-second timeout.

    Because the server drops those filters silently, ``search`` is called with
    ``require_applied=False``. That check exists to stop a query from quietly
    covering a wider slice than requested; here the whole table *is* what we
    want, so the exception is deliberate rather than an oversight.

    Correctness therefore cannot come from the filter, and has to come from
    inspecting what came back. Two guards do that. The row count must reach
    :data:`PAYMENTS_MIN_ROWS`, since a truncated transfer or a server-side
    change that returned only part of the table would land far below the ~867k
    rows this endpoint has been returning. And every row must carry the keys in
    :data:`PAYMENTS_REQUIRED_KEYS`, since a renamed field would otherwise parse
    into a column of nulls. Either failure raises rather than writing the file:
    a short pull that overwrote the snapshot would drop vendors and dollars out
    of every concentration figure without anything downstream looking wrong.

    Raises:
        PortalError: If the response is implausibly small, or rows lack the
            fields the analysis reads.
    """
    rows = client.search("payments", [PAYMENTS_FILTER], require_applied=False)
    log.info("payments: %d rows", len(rows))

    if len(rows) < PAYMENTS_MIN_ROWS:
        raise PortalError(
            f"payments search returned {len(rows):,} rows, below the {PAYMENTS_MIN_ROWS:,} "
            "expected for the full table. The module has no working filters, so a short "
            "result means a partial or truncated response, not a narrower query. "
            "Refusing to overwrite the snapshot."
        )

    for row in rows:
        missing = PAYMENTS_REQUIRED_KEYS - set(row)
        if missing:
            raise PortalError(
                f"payment row {row.get('id', '?')} is missing {sorted(missing)}; "
                "the response shape changed and parse.load_payments would read nulls."
            )

    path = out_dir / "payments.json"
    merged, retained = retain_by_group(path, rows, lambda r: r["id"])
    _write(path, merged)
    return Snapshot(
        name="payments",
        pulled_at=_now(),
        rows=len(merged),
        detail={
            "filter_sent": {PAYMENTS_FILTER.name: PAYMENTS_FILTER.value},
            "filter_applied_server_side": False,
            "min_expected_rows": PAYMENTS_MIN_ROWS,
            "listed": len(rows),
            "retained_delisted": retained,
            "with_contract_number": sum(1 for r in merged if r.get("contractNumber")),
        },
    )


def solicitation_years(today: date | None = None) -> list[str]:
    """FY2013 through the current fiscal year, which starts on 1 October."""
    today = today or date.today()
    current = today.year + 1 if today.month >= 10 else today.year
    return [f"FY{y}" for y in range(2013, current + 1)]


def write_manifest(snapshots: list[Snapshot], out_dir: Path = RAW_DIR) -> None:
    """Record what was pulled, when, so an analysis can cite its snapshot."""
    _write(
        out_dir / "manifest.json",
        {
            "pulled_on": date.today().isoformat(),
            "source": "https://contracts.ocp.dc.gov",
            "snapshots": [s.as_dict() for s in snapshots],
        },
    )


def update_manifest_entry(snapshot: Snapshot, out_dir: Path = RAW_DIR) -> None:
    """Replace one snapshot's entry in an existing manifest, leaving the rest.

    Pulling a single module on its own would otherwise leave the manifest
    describing the file that used to be on disk. The top-level ``pulled_on``
    date is not touched, because the other files really were pulled then; each
    entry carries its own ``pulled_at``.
    """
    path = out_dir / "manifest.json"
    if not path.exists():
        write_manifest([snapshot], out_dir)
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = [e for e in manifest.get("snapshots", []) if e.get("name") != snapshot.name]
    manifest["snapshots"] = entries + [snapshot.as_dict()]
    _write(path, manifest)


def all_commodity_classes(client: DCProcurementClient) -> list[str]:
    """Every three-digit NIGP class the portal knows about.

    Pulling contracts class by class across all 266 is the only way to reach the
    whole contract universe through filters the server actually applies. It also
    records which class each contract was found under, which is how the coding
    inconsistencies in :mod:`dcprocure.commodities` were found in the first
    place.
    """
    return [c["code"] for c in client.commodities()]


def pull_all(out_dir: Path = RAW_DIR, all_classes: bool = False) -> list[Snapshot]:
    """Pull everything the analysis needs, in dependency order.

    Args:
        out_dir: Where raw JSON is written.
        all_classes: Pull every NIGP class rather than only the technology ones.
            Needed to classify the whole contract universe, which is what makes
            a share-of-spend figure meaningful rather than a share of whatever
            the technology classes happened to capture.
    """
    snapshots: list[Snapshot] = []
    with DCProcurementClient() as client:
        pull_reference(client, out_dir)
        classes = all_commodity_classes(client) if all_classes else list(IT_ALL)

        contracts = pull_by_commodity(client, "contracts", classes, out_dir)
        snapshots.append(contracts)

        # Every class, like contracts: limiting orders to the IT-related
        # classes built a definition of technology into the data itself. The
        # full table is about 240,000 orders, a minute or so to pull.
        orders = pull_by_commodity(client, "purchase", classes, out_dir)
        snapshots.append(orders)

        contract_rows = json.loads(
            (out_dir / "contracts_by_commodity.json").read_text(encoding="utf-8")
        )
        ids = [row["id"] for row in contract_rows]
        snapshots.append(pull_contract_details(client, ids, out_dir))

        # The archive begins at FY2013: it holds 1,321 rows for FY2013 and
        # about 1,300 a year after, against 46 for FY2012 and single digits
        # before that. Those earlier years are the portal starting to keep
        # records, not the District pausing, so FY2013 is where the usable
        # history starts and there is no reason to stop at FY2016.
        years = solicitation_years()
        snapshots.append(pull_solicitations(client, years, out_dir))

        snapshots.append(pull_payments(client, out_dir))

    write_manifest(snapshots, out_dir)
    return snapshots
