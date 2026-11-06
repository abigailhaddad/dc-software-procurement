"""Build and publish the public Hugging Face dataset of DC procurement records.

``dc-procurement-raw`` is the working archive: portal responses, kept after the
portal drops them. This is the copy for people who want to use the data: one
Parquet file per table, a dataset card, and nothing else.

The tables are the ones the site reads (see :mod:`dcprocure.export`), minus the
two link tables the site builds for speed and minus a few columns listed in
:data:`DROP_COLUMNS`. Nothing is recomputed here. Every ID column stays text, so
leading zeros survive, and every value stays as the portal published it.

``build`` writes the files to a local folder and verifies them. ``upload`` makes
one commit to the dataset. ``dcprocure publish-dataset`` is a dry run (build and
verify only) unless given ``--upload``.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from .classify import _load_dotenv
from .config import GITHUB_REPO, HF_ARCHIVE_REPO, HF_DATASET_REPO, PORTAL, SITE_URL

# table -> (columns that identify a row, the summary.json entry and field that
# should equal its row count).
TABLES: dict[str, tuple[tuple[str, ...], tuple[str, str] | None]] = {
    "contracts": (("id",), ("contracts", "periods")),
    "purchase_orders": (("po_number",), ("purchase_orders", "orders")),
    "payments": (("id",), ("payments", "lines")),
    "solicitations": (("solicitation_number",), ("solicitations", "solicitations")),
    "documents": (("document_id",), None),
    "nigp_classes": (("nigp_class",), None),
}

# Columns left out of the public tables. Change this list, not the code below.
#
# * of_interest, of_interest_detail: hand-written notes on the few contracts the
#   project's leads script nominated. A blank means "not read", not "nothing
#   found", which is easy to misread outside the site that explains it.
# * specialist_email, specialist_phone: staff work contact details. They are
#   public on the portal and of little use for analysis.
#
# The names of contracting officers and solicitation specialists stay: the
# portal publishes them, and who ran a procurement is part of the record.
DROP_COLUMNS: dict[str, tuple[str, ...]] = {
    "contracts": ("of_interest", "of_interest_detail"),
    "solicitations": ("specialist_email", "specialist_phone"),
}

DATA_GOV_ENTRY = "https://catalog.data.gov/dataset/contracts-and-procurement-transparency-portal"


class PublishError(RuntimeError):
    """The built tables do not match the archive they came from."""


def _q(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _kept_columns(con: duckdb.DuckDBPyConnection, src: Path, table: str) -> list[str]:
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {_q(src)}").fetchall()]
    dropped = set(DROP_COLUMNS.get(table, ()))
    missing = dropped - set(cols)
    if missing:
        raise PublishError(f"{table}: DROP_COLUMNS names {sorted(missing)}, which the table lacks")
    return [c for c in cols if c not in dropped]


def build(source_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Write the public tables and card to ``out_dir`` and verify them.

    Args:
        source_dir: A folder with the export's Parquet tables, ``columns.json`` and
            ``summary.json`` (``data/raw/parquet``, or ``archive/parquet`` of the
            archive dataset).
        out_dir: Where to write ``data/<table>.parquet`` and ``README.md``.

    Returns:
        What was built: row counts, file sizes and the checks that passed.

    Raises:
        PublishError: When a built table does not match its source.
    """
    source_dir, out_dir = Path(source_dir), Path(out_dir)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    summary = json.loads((source_dir / "summary.json").read_text(encoding="utf-8"))
    described = json.loads((source_dir / "columns.json").read_text(encoding="utf-8"))

    report: dict[str, Any] = {"tables": {}, "checks": []}
    for table, (key, summary_ref) in TABLES.items():
        src = source_dir / f"{table}.parquet"
        dst = out_dir / "data" / f"{table}.parquet"
        cols = _kept_columns(con, src, table)
        select = ", ".join(f'"{c}"' for c in cols)
        con.execute(f"COPY (SELECT {select} FROM {_q(src)}) TO {_q(dst)} (FORMAT PARQUET, COMPRESSION ZSTD)")
        report["tables"][table] = _verify(con, table, key, summary_ref, src, dst, cols, summary, report["checks"])
        report["tables"][table]["bytes"] = dst.stat().st_size

    card, facts = _card(con, out_dir, summary, described)
    (out_dir / "README.md").write_text(card, encoding="utf-8")
    report["as_of"] = facts["as_of"]
    return report


def _verify(con, table, key, summary_ref, src, dst, cols, summary, checks) -> dict[str, Any]:
    """Compare a built table with the source table, and with the export's own summary."""
    def one(sql: str):
        return con.execute(sql).fetchone()

    n_src = one(f"SELECT count(*) FROM {_q(src)}")[0]
    n_dst = one(f"SELECT count(*) FROM {_q(dst)}")[0]
    if n_src != n_dst:
        raise PublishError(f"{table}: {n_dst:,} rows built from {n_src:,}")
    checks.append(f"{table}: {n_dst:,} rows, same as the source")

    if summary_ref:
        group, field = summary_ref
        expected = summary[group][field]
        if expected != n_dst:
            raise PublishError(f"{table}: summary.json says {expected:,} {field}, the table has {n_dst:,}")
        checks.append(f"{table}: matches summary.json {group}.{field}")

    keys = ", ".join(f'"{k}"' for k in key)
    # Same key values, with the same multiplicity, in both tables.
    differ = one(
        f"""SELECT count(*) FROM (
              (SELECT {keys} FROM {_q(src)} EXCEPT ALL SELECT {keys} FROM {_q(dst)})
              UNION ALL
              (SELECT {keys} FROM {_q(dst)} EXCEPT ALL SELECT {keys} FROM {_q(src)}))"""
    )[0]
    if differ:
        raise PublishError(f"{table}: {differ:,} key values differ between the source and the built table")
    checks.append(f"{table}: every {'/'.join(key)} matches the source, with repeats")

    # Row by row on the kept columns, not just on the keys.
    select = ", ".join(f'"{c}"' for c in cols)
    differ = one(
        f"""SELECT count(*) FROM (
              (SELECT {select} FROM {_q(src)} EXCEPT ALL SELECT {select} FROM {_q(dst)})
              UNION ALL
              (SELECT {select} FROM {_q(dst)} EXCEPT ALL SELECT {select} FROM {_q(src)}))"""
    )[0]
    if differ:
        raise PublishError(f"{table}: {differ:,} rows differ from the source on the kept columns")
    checks.append(f"{table}: all {len(cols)} kept columns equal the source, row for row")

    # Identifiers must be text: Excel and pandas turn leading zeros into numbers.
    types = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM {_q(dst)}").fetchall()}
    id_cols = [c for c in types if c in set(key) or c.endswith(("_number", "_id", "_code", "_class")) or c == "id"]
    bad = {c: types[c] for c in id_cols if types[c] != "VARCHAR"}
    if bad:
        raise PublishError(f"{table}: identifier columns are not text: {bad}")
    checks.append(f"{table}: identifier columns are text")

    return {"rows": n_dst, "columns": len(cols)}


def _range(con, table_path: Path, col: str) -> tuple[str, str]:
    lo, hi = con.execute(f'SELECT min("{col}"), max("{col}") FROM {_q(table_path)}').fetchone()
    return str(lo), str(hi)


def _card(con, out_dir: Path, summary: dict, described: dict) -> tuple[str, dict[str, Any]]:
    data = out_dir / "data"
    n = {t: con.execute(f"SELECT count(*) FROM {_q(data / (t + '.parquet'))}").fetchone()[0] for t in TABLES}
    as_of = con.execute(f"SELECT max(last_listed) FROM {_q(data / 'contracts.parquet')}").fetchone()[0]
    c_start = _range(con, data / "contracts.parquet", "start_date")
    c_award = _range(con, data / "contracts.parquet", "award_date")
    p_dates = _range(con, data / "payments.parquet", "payment_date")
    o_dates = _range(con, data / "purchase_orders.parquet", "ordered_date")
    s_close = _range(con, data / "solicitations.parquet", "closing_date")
    delisted = {
        t: con.execute(f"SELECT count(*) FROM {_q(data / (t + '.parquet'))} WHERE listed = false").fetchone()[0]
        for t in ("contracts", "purchase_orders", "payments")
    }
    payment_ids = summary["payments"]["ids"]
    with_contract = 1 - summary["payments"]["no_contract"] / summary["payments"]["lines"]
    check_ratio = summary["payments"]["check_totals"] / summary["payments"]["paid"]
    recovered = con.execute(
        f"SELECT count(*) FROM {_q(data / 'contracts.parquet')} WHERE recovered_on IS NOT NULL"
    ).fetchone()[0]
    contracts_distinct = summary["contracts"]["contracts"]

    def column_table(table: str) -> str:
        kept = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {_q(data / (table + '.parquet'))}").fetchall()]
        notes = described.get(table, {})
        rows = [f"| `{c}` | {str(notes.get(c, '')).replace('|', '/')} |" for c in kept]
        return "| Column | Where it comes from |\n|---|---|\n" + "\n".join(rows)

    dropped_note = "; ".join(f"`{t}`: {', '.join(f'`{c}`' for c in cols)}" for t, cols in DROP_COLUMNS.items())

    card = f"""---
license: other
license_name: dc-ocp-public-records
language:
  - en
tags:
  - government
  - procurement
  - district-of-columbia
  - contracts
  - payments
pretty_name: DC contracts, purchase orders and payments
size_categories:
  - 1M<n<10M
configs:
  - config_name: contracts
    data_files: data/contracts.parquet
    default: true
  - config_name: purchase_orders
    data_files: data/purchase_orders.parquet
  - config_name: payments
    data_files: data/payments.parquet
  - config_name: solicitations
    data_files: data/solicitations.parquet
  - config_name: documents
    data_files: data/documents.parquet
  - config_name: nigp_classes
    data_files: data/nigp_classes.parquet
---

# DC contracts, purchase orders and payments

Records published by the District of Columbia's Office of Contracting and
Procurement (OCP) on its [Contracts and Procurement Transparency Portal]({PORTAL}),
as Parquet tables. This is an unofficial copy. The portal is the authority; where
the two disagree, the portal is right.

* Code and method: https://github.com/{GITHUB_REPO}
* Explorer: {SITE_URL}
* Raw portal responses and weekly history: https://huggingface.co/datasets/{HF_ARCHIVE_REPO}

## As of

Pulled from the portal on **{as_of}**. Rebuilt weekly, so the numbers below are
for that pull and will move.

## Tables

| Table | Rows | One row is | Covers |
|---|---|---|---|
| `contracts` | {n['contracts']:,} | One option period of a contract ({contracts_distinct:,} contracts) | Periods starting {c_start[0]} to {c_start[1]}; awarded {c_award[0]} to {c_award[1]} |
| `purchase_orders` | {n['purchase_orders']:,} | One purchase order | Ordered {o_dates[0]} to {o_dates[1]} |
| `payments` | {n['payments']:,} | One voucher line ({payment_ids:,} distinct `id`s) | Paid {p_dates[0]} to {p_dates[1]} |
| `solicitations` | {n['solicitations']:,} | One solicitation, open or archived | Closing {s_close[0]} to {s_close[1]} |
| `documents` | {n['documents']:,} | One document attached to a contract | Links to the portal; the files are not copied |
| `nigp_classes` | {n['nigp_classes']:,} | One commodity class | Lookup for the NIGP codes |

All identifiers (`id`, `contract_number`, `po_number`, `document_id`, ...) are
text, exactly as the portal publishes them. Open the files as Parquet. Amounts
and dates are parsed from the portal's text, and each parsed column sits beside
the text it came from.

## How it is collected and kept

A GitHub Action pulls the portal every Monday: every commodity class for
contracts and purchase orders, the whole payments table, and open and archived
solicitations. A pull is **merged into the archive, never swapped for it.** A row
the portal stops returning is kept and marked `listed = false`, with `first_listed`
and `last_listed` dates. {delisted['contracts']:,} contract periods, {delisted['purchase_orders']:,} purchase
orders and {delisted['payments']:,} payment rows are marked that way in this pull.

The portal is a rolling window, not an archive. On 1 October 2026, when the
fiscal year turned, it stopped returning part of its contract periods.
{recovered:,} were put back from earlier pulls and from the portal's detail pages,
and are marked with `recovered_on`. The earliest contract period in this data
starts {c_start[0]}.

Payment `id`s repeat, so do not de-duplicate on `id`. A revised payment comes back
under its old `id` and replaces its old rows. A revised purchase order comes back
under a new `-Vn` number, and the old version is replaced.

Before anything is published, the tables are checked against the raw archive
(counts, totals, a sample of rows field by field). A pull that returns far fewer
rows than the archive holds fails and publishes nothing.

## Things that are easy to get wrong

* **A contract row is an option period, not a contract.** Summing `amount` across
  rows double counts. `base_amount` and `period_kind` mark the base period.
* **Most payments carry no contract number.** {with_contract:.0%} do, so a join from
  payments to contracts covers about that share of the payment lines.
* **`voucher_amount` and `check_total` are different.** `check_total` repeats on every
  line the check covers. Summing it gives about {check_ratio:.1f} times the amount
  actually paid.
* **Commodity codes are assigned by the buyer and applied loosely.**
* **Option-period amounts often disagree with the contract paperwork.** The project
  shows only base-period amounts, and `corrected_amount`, `same_award_as` and `correction`
  record hand-checked errors, each with a source in `corrections.yaml` in the repo.
* **Payment vendor names are often cut off at a fixed length**, as the portal gives them.
* **Names are as published.** Agency names the portal gives only as an abbreviation or
  variant spelling are mapped to one name in `agency`, and the published value stays in
  `agency_as_published`.
* **The portal's data has gaps.** It does not publish everything the District buys, and
  this copy cannot be more complete than the portal.

## People in the data

The portal publishes the names of contracting officers and solicitation specialists,
and the names of vendors and payees, which include individual people and sole
practitioners (physicians and consultants paid under their own names, for example).
They are here as the portal has them. Left out of this copy: {dropped_note}.

## Terms

OCP publishes these records on its portal. This copy adds no license. The
[data.gov catalog entry]({DATA_GOV_ENTRY}) for the portal, published by OCP, lists
CC BY 4.0, as of its last modification (2022-02-16). That is the catalog's
description of the portal data. The portal's own terms were not found when this
card was written. Check them before reusing the data, and credit the District of
Columbia Office of Contracting and Procurement.

## Columns

{chr(10).join(f'### `{t}`{chr(10)}{chr(10)}{column_table(t)}{chr(10)}' for t in TABLES if t != 'nigp_classes')}
"""
    return card, {"as_of": str(as_of), "rows": n}


def upload(out_dir: Path, repo_id: str = HF_DATASET_REPO) -> str:
    """Commit the built files to the public dataset in one commit; create it if missing.

    Requires ``HF_TOKEN`` with write access to ``repo_id``'s owner.

    Returns:
        The commit's URL.
    """
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi
    from huggingface_hub.errors import RepositoryNotFoundError

    _load_dotenv()
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set; nothing was uploaded")
    api = HfApi(token=token)
    out_dir = Path(out_dir)
    wanted = {"README.md": out_dir / "README.md"} | {
        f"data/{p.name}": p for p in sorted((out_dir / "data").glob("*.parquet"))
    }
    if len(wanted) != len(TABLES) + 1:
        raise PublishError(f"expected {len(TABLES)} tables and a card in {out_dir}, found {sorted(wanted)}")

    parent = None
    existing: set[str] = set()
    try:
        info = api.dataset_info(repo_id)
        parent = info.sha
        existing = {s.rfilename for s in info.siblings or []}
    except RepositoryNotFoundError:
        api.create_repo(repo_id, repo_type="dataset", private=False)

    operations: list[Any] = [CommitOperationAdd(path_in_repo=k, path_or_fileobj=str(v)) for k, v in wanted.items()]
    # A table dropped from TABLES leaves its file behind; remove it in the same commit.
    operations += [
        CommitOperationDelete(path_in_repo=f)
        for f in sorted(existing)
        if f.startswith("data/") and f not in wanted
    ]
    commit = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=operations,
        commit_message=f"Weekly refresh, pulled {date.today().isoformat()}",
        parent_commit=parent,
    )
    return commit.commit_url
