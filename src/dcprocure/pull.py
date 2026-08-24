"""Pull raw data from the transparency portal onto disk.

Everything lands as raw JSON under ``data/raw`` alongside a manifest recording
when it was pulled and what was asked for. Analysis reads from disk, never from
the network, so a result can be re-derived later from the same snapshot.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

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
    """Pull the lookup tables: commodity classes and agency clusters."""
    _write(out_dir / "commodities.json", client.commodities())
    _write(out_dir / "agencies.json", client.agencies())


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

    records = list(seen.values())
    _write(out_dir / f"{module}_by_commodity.json", records)
    return Snapshot(
        name=f"{module}_by_commodity",
        pulled_at=_now(),
        rows=len(records),
        detail={"classes": per_class, "rows_before_dedupe": sum(per_class.values())},
    )


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

        orders = pull_by_commodity(client, "purchase", IT_ALL, out_dir)  # IT only: PO volume is large
        snapshots.append(orders)

        contract_rows = json.loads(
            (out_dir / "contracts_by_commodity.json").read_text(encoding="utf-8")
        )
        ids = [row["id"] for row in contract_rows]
        snapshots.append(pull_contract_details(client, ids, out_dir))

        years = [f"FY{y}" for y in range(2016, 2027)]
        snapshots.append(pull_solicitations(client, years, out_dir))

    write_manifest(snapshots, out_dir)
    return snapshots
