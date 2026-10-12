"""Keep every record the portal has ever shown us, after it stops showing it.

The portal is a rolling window, not an archive. On 1 October 2026, when FY2027
began, the contracts search stopped returning 1,297 of the 7,165 option-period
rows in the September pull -- every period of 647 contracts, among them CW91684,
the rate-card gold contract, and CW84401, the DC Supply Schedule catalog, and
the oldest periods of 635 contracts it still lists. Every contract still listed ended on
or after 18 November 2021. The payments table starts on 6 October 2016, the
first week of FY2017, which looks like the same kind of window.

So a pull must never be a replacement. Each pulled file is merged with what was
already on disk: a row the portal still returns is refreshed, and a row it no
longer returns is kept, marked ``listed: False`` with the last date it was seen.
The detail endpoint still answers for delisted contracts, which is how the rows
lost before this module existed were recovered (:func:`recover_delisted`).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from .client import DCProcurementClient, PortalError

log = logging.getLogger(__name__)

Record = dict[str, Any]

# Bookkeeping fields this module adds to the portal's rows.
ARCHIVE_FIELDS = ("listed", "first_listed", "last_listed", "recovered_on")


def _load(path: Path) -> list[Record]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def retain_by_key(
    path: Path,
    current: list[Record],
    key: Callable[[Record], str],
    today: str | None = None,
) -> tuple[list[Record], int]:
    """Merge a fresh pull with the file already at ``path``.

    Rows in ``current`` win on content and are stamped listed today. Rows only
    in the old file are kept unchanged except for ``listed: False``.

    Returns:
        The merged rows (current first, then retained) and how many were
        retained.
    """
    today = today or date.today().isoformat()
    previous = {key(r): r for r in _load(path)}
    merged: list[Record] = []
    seen: set[str] = set()
    for row in current:
        k = key(row)
        seen.add(k)
        old = previous.get(k, {})
        merged.append(
            {
                **row,
                "listed": True,
                "first_listed": old.get("first_listed") or today,
                "last_listed": today,
                **({"recovered_on": old["recovered_on"]} if old.get("recovered_on") else {}),
            }
        )
    retained = [{**r, "listed": False} for k, r in previous.items() if k not in seen]
    return merged + retained, len(retained)


def retain_by_group(
    path: Path,
    current: list[Record],
    key: Callable[[Record], str],
    today: str | None = None,
) -> tuple[list[Record], int]:
    """Merge rows whose key is shared by several rows, as payment ids are.

    A payment ``id`` is not unique: the October 2026 pull held 879,641 rows and
    865,562 distinct ids. Nor is a row stable: 2,187 rows of the September pull
    came back under the same id with different content, payments the District
    had since updated. So the unit is the id's whole group of rows. If the pull
    returns an id at all, its rows replace the old ones; only an id the pull no
    longer returns has its old rows kept. Matching on content instead would keep
    the old version of every edited payment beside the new one, and every sum
    over the table would count it twice.
    """
    today = today or date.today().isoformat()
    now = {key(r) for r in current}
    merged = [{**r, "listed": True, "last_listed": today} for r in current]
    retained = [{**r, "listed": False} for r in _load(path) if key(r) not in now]
    return merged + retained, len(retained)


def contract_id(number: str, period: str) -> str:
    """The portal's opaque contract id: base64 of ``number¦period``.

    Not documented anywhere; read off the ids the search returns, e.g.
    ``Q1cxMzA1OTTCpkJhc2UgUGVyaW9k`` is ``CW130594¦Base Period``.
    """
    return base64.b64encode(f"{number}¦{period}".encode()).decode()


# The names cover 97% of listed periods. The rest are free text ("Option Year
# One", "Base Year ($107,000.00)") and cannot be guessed, so a contract whose
# periods were named that way may come back partial or not at all; the summary
# says how many.
STANDARD_PERIODS = ("Base Period", *(f"Option {n}" for n in range(1, 7)))
FALLBACK_PERIODS = (
    "Base",
    "Base Year",
    "Base period",
    *(f"Option Year {n}" for n in range(1, 5)),
)


def _amount(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(str(value).replace("$", "").replace(",", ""))


def _end(record: Record) -> date:
    month, day, year = (int(x) for x in record["endDate"].split("/"))
    return date(year, month, day)


def split_to_corpus(
    found: list[Record],
    expected: dict[str, tuple[int, float]],
    on_disk: dict[str, list[Record]] | None = None,
) -> tuple[list[Record], list[Record]]:
    """Separate the periods the analysis had from older ones it never saw.

    The detail endpoint answers for periods the search had already stopped
    listing before this project's first pull: CW41352 had one period in the
    September 2026 snapshot and five come back by id. Putting those back would
    move published ceilings for a reason unrelated to the District. So only the
    latest-ending ``k`` periods are restored, where ``k`` and the ceiling are
    what the analysis last recorded, and only when those periods sum to that
    ceiling to the cent. Of the 336 contracts recovered with extra periods, the
    75 whose amounts single out one set of periods were the latest-ending ``k``
    every time. Everything else is returned separately to be kept, not used.

    A contract can also still be listed with its oldest periods dropped, as 635
    were on 1 October 2026. ``on_disk`` holds the
    periods already on disk per contract; only periods ending before the
    earliest of those are candidates, only as many as are missing, and the
    disk periods count toward the ceiling check.
    """
    on_disk = on_disk or {}
    by_number: dict[str, list[Record]] = {}
    for record in found:
        by_number.setdefault(record["contractNumber"], []).append(record)
    corpus: list[Record] = []
    older: list[Record] = []
    for number, records in by_number.items():
        if number not in expected:
            corpus += records
            continue
        k, ceiling = expected[number]
        have = on_disk.get(number, [])
        if have:
            first = min(_end(r) for r in have)
            candidates = [r for r in records if _end(r) < first]
        else:
            candidates = records
        need = k - len(have)
        latest = sorted(candidates, key=_end)[-need:] if need > 0 else []
        total = sum(_amount(r["contractAmount"]) for r in have + latest)
        if need > 0 and len(latest) == need and abs(total - ceiling) < 0.01:
            keep = {r["id"] for r in latest}
            corpus += latest
            older += [r for r in records if r["id"] not in keep]
        else:
            older += records
    return corpus, older


def _recount_manifest(raw_dir: Path, today: str) -> None:
    """Make the manifest's row counts describe the files after a restore.

    verify_claims checks the manifest against the files on disk. A restore adds
    rows after the pull wrote the manifest, so the counts are updated here; the
    pull dates are left alone, because the pull is still when those were taken.
    """
    path = raw_dir / "manifest.json"
    if not path.exists():
        return
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry in manifest.get("snapshots", []):
        file = raw_dir / f"{entry.get('name')}.json"
        if entry.get("name") in (
            "contracts_by_commodity", "contract_details", "purchase_by_commodity", "payments"
        ) and file.exists():
            rows = _load(file)
            entry["rows"] = len(rows)
            entry["recovered"] = sum(1 for r in rows if r.get("recovered_on"))
    manifest["recovered_on"] = today
    path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def recover_delisted(
    client: DCProcurementClient,
    numbers: Iterable[str],
    raw_dir: Path,
    expected: dict[str, tuple[int, float]] | None = None,
    workers: int = 6,
) -> dict[str, Any]:
    """Fetch detail records for contracts, or periods, the search no longer lists.

    Tries the standard period names for each contract, and the fallback names
    only for contracts where none of the standard ones answered. Periods the
    analysis had (see :func:`split_to_corpus`; all of them if ``expected`` is
    None) are added to ``contract_details.json`` and, rebuilt from the detail
    record, to ``contracts_by_commodity.json``, both marked ``listed: False``.
    The rest go to ``contract_details_older_periods.json``, which no analysis
    reads.
    """
    today = date.today().isoformat()
    details_path = raw_dir / "contract_details.json"
    search_path = raw_dir / "contracts_by_commodity.json"
    details = _load(details_path)
    search = _load(search_path)
    have = {d["id"] for d in details}
    numbers = sorted(set(numbers))

    def probe(ids: list[str]) -> list[Record]:
        def fetch(cid: str) -> Record | None:
            if cid in have:
                return None
            try:
                record = client.contract_details(cid)
            except (PortalError, ValueError):
                # A period that does not exist comes back as an empty body,
                # which fails JSON decoding.
                return None
            return record if record and record.get("id") == cid else None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [r for r in pool.map(fetch, ids) if r]

    found = probe([contract_id(n, p) for n in numbers for p in STANDARD_PERIODS])
    hit = {r["contractNumber"] for r in found}
    missing = [n for n in numbers if n not in hit]
    found += probe([contract_id(n, p) for n in missing for p in FALLBACK_PERIODS])
    on_disk: dict[str, list[Record]] = {}
    for d in details:
        on_disk.setdefault(d["contractNumber"], []).append(d)
    corpus, older = split_to_corpus(found, expected or {}, on_disk)
    restored = {r["contractNumber"] for r in corpus}

    stamp = {"listed": False, "recovered_on": today}
    details += [{**r, **stamp} for r in corpus]
    older_path = raw_dir / "contract_details_older_periods.json"
    older_ids = {r["id"] for r in _load(older_path)}
    older_rows = _load(older_path) + [{**r, **stamp} for r in older if r["id"] not in older_ids]
    search_ids = {r["id"] for r in search}
    for r in corpus:
        if r["id"] in search_ids:
            continue
        row = {k: r.get(k) for k in SEARCH_FIELDS}
        # Rebuilt from the detail record, which is not the search's format:
        # commodityCodes here lists every code with its description, where
        # the search often lists only some, so the NIGP classes the analysis
        # reads can come out wider. restore_from_snapshot replaces these rows
        # wherever an earlier pull has the real one. matched_classes is the
        # codes' own prefixes, since the search never returned this row.
        row["matched_classes"] = sorted({c[:3] for c in r.get("commodityCodes") or []})
        search.append({**row, **stamp})

    for path, rows in (
        (details_path, details),
        (search_path, search),
        (older_path, older_rows),
    ):
        path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    _recount_manifest(raw_dir, today)

    found_numbers = {r["contractNumber"] for r in found}
    return {
        "asked": len(numbers),
        "contracts_restored": len(restored),
        "periods_restored": len(corpus),
        "older_periods_kept_aside": len(older),
        "not_found": sorted(set(numbers) - found_numbers),
        "found_but_not_reconciled": sorted(found_numbers - restored),
    }


def restore_from_snapshot(raw_dir: Path, snapshot_dir: Path) -> dict[str, Any]:
    """Put back every period an earlier pull held and the current files lack.

    ``snapshot_dir`` is an earlier ``data/raw`` -- the copy published to
    Hugging Face before the drop. This is exact where :func:`recover_delisted`
    has to guess at period names: it restored 1,287 of the periods lost on
    1 October 2026, identical field for field to the September copy, and could
    not reach the 10 whose periods were named in free text ("Base year + Option
    Year 4", "3"). The snapshot's search rows also replace the ones
    :func:`recover_delisted` rebuilt from detail records, which differ.
    """
    today = date.today().isoformat()
    details_path = raw_dir / "contract_details.json"
    search_path = raw_dir / "contracts_by_commodity.json"
    details = _load(details_path)
    search = _load(search_path)
    old_details = {r["id"]: r for r in _load(snapshot_dir / "contract_details.json")}
    old_search = {r["id"]: r for r in _load(snapshot_dir / "contracts_by_commodity.json")}

    have = {r["id"] for r in details}
    stamp = {"listed": False, "recovered_on": today}
    added = [old_details[i] for i in old_details if i not in have]
    details += [{**r, **stamp} for r in added]

    # Equal field for field is not enough. The detail endpoint lists a
    # period's documents in no fixed order (363 of the 1,297 recovered periods
    # came back reordered), and the award picker breaks ties between
    # same-named copies by position, so CW48317 picked a copy whose OCR text was
    # never cached and lost 210 rates. The picker also takes a contract's first
    # record in file order, which the portal gives newest period first, and
    # recover_delisted appended oldest first. So recovered records are replaced
    # whole by the snapshot's and placed in the snapshot's order, after every
    # record the portal still lists.
    position = {i: n for n, i in enumerate(old_details)}
    recovered = [
        {**old_details[r["id"]], "listed": False, "recovered_on": r["recovered_on"]}
        if r["id"] in old_details else r
        for r in details if r.get("recovered_on")
    ]
    recovered.sort(key=lambda r: position.get(r["id"], len(position)))
    details = [r for r in details if not r.get("recovered_on")] + recovered

    search_by_id = {r["id"]: r for r in search}
    # A search row rebuilt from a detail record is not what the search returned:
    # the detail lists every commodity code with its description, the search
    # often only some of them, bare (12% of listed rows differ). The analysis
    # reads NIGP classes off the search row, so a rebuilt row moved CW90472 from
    # class 918 to 918 and 920 and the classifier relabelled it. Wherever the
    # snapshot has the real row, it replaces the rebuilt one whole.
    corrected = 0
    for i, r in enumerate(search):
        if r.get("recovered_on") and r["id"] in old_search:
            search[i] = {**old_search[r["id"]], "listed": False, "recovered_on": r["recovered_on"]}
            corrected += 1
    for r in added:
        if r["id"] not in search_by_id and r["id"] in old_search:
            search.append({**old_search[r["id"]], **stamp})

    # A period restored here may have been set aside earlier as unreconciled.
    older_path = raw_dir / "contract_details_older_periods.json"
    older = [r for r in _load(older_path) if r["id"] not in old_details]

    for path, rows in ((details_path, details), (search_path, search), (older_path, older)):
        path.write_text(json.dumps(rows, indent=1), encoding="utf-8")

    # Payments and orders the snapshot holds and the portal no longer returns
    # at all, by the same groups the weekly merge uses. Between September and
    # October 2026 that was one order, PO678534, and its four $25,000 payments
    # (Criminal Code Reform Commission to the Justice Policy Institute, 2023),
    # plus PO740499: removed, not aged out, and not re-filed under new ids.
    from .pull import _po_base

    other = {}
    for name, key in (
        ("payments.json", lambda r: r["id"]),
        ("purchase_by_commodity.json", lambda r: _po_base(r["poNumber"])),
    ):
        if not (snapshot_dir / name).exists() or not (raw_dir / name).exists():
            continue
        rows = _load(raw_dir / name)
        now = {key(r) for r in rows}
        back = [{**r, **stamp} for r in _load(snapshot_dir / name) if key(r) not in now]
        if back:
            (raw_dir / name).write_text(json.dumps(rows + back, indent=1), encoding="utf-8")
        other[name] = len(back)

    _recount_manifest(raw_dir, today)
    return {
        "periods_restored": len(added),
        "contracts": sorted({r["contractNumber"] for r in added}),
        "search_rows_replaced": corrected,
        "rows_restored": other,
    }


SEARCH_FIELDS = (
    "id",
    "contractNumber",
    "title",
    "agencyNames",
    "currentOptionPeriod",
    "startDate",
    "endDate",
    "awardDate",
    "commodityCodes",
    "vendor",
    "contractAmount",
)
