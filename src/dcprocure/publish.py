"""Publish the cumulative portal files to the project's Hugging Face dataset.

``data/raw`` is not tracked in git and lives on one laptop, and the portal
forgets contracts as they age out (see :mod:`dcprocure.archive`). The
``archive/`` directory of ``abigailhaddad/dc-procurement-raw`` is the copy that
survives both. It is replaced in full each run; the files only ever grow, so
the latest holds everything, and the dataset's commit history holds each week.

The dataset's rule is that nothing in it is derived. The archive files are the
portal's responses with four bookkeeping fields added and no other change, and
the dataset card says so. Classifications and other model output stay local.

Requires ``HF_TOKEN`` with write access, in the environment or the project
``.env``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .classify import _load_dotenv
from .pull import RAW_DIR

log = logging.getLogger(__name__)

REPO_ID = "abigailhaddad/dc-procurement-raw"

FILES = (
    "contracts_by_commodity.json",
    "contract_details.json",
    "contract_details_older_periods.json",
    "purchase_by_commodity.json",
    "payments.json",
    "solicitations.json",
    "agencies.json",
    "commodities.json",
    "manifest.json",
    # The same rows as Parquet, for the site's explorer: see dcprocure.export.
    "parquet/contracts.parquet",
    "parquet/documents.parquet",
    "parquet/purchase_orders.parquet",
    "parquet/payments.parquet",
    "parquet/nigp_classes.parquet",
    "parquet/contract_payments.parquet",
    "parquet/contract_orders.parquet",
    "parquet/columns.json",
    "parquet/summary.json",
)

GROWS_ONLY = frozenset(
    {
        "contracts_by_commodity.json",
        "contract_details.json",
        "purchase_by_commodity.json",
        "payments.json",
    }
)

TABLE_ROW = "| `archive/` | Every row the portal has ever returned, kept after it stops returning it; refreshed weekly |"

SECTION_START = "<!-- archive section: written by dcprocure publish -->"
SECTION_END = "<!-- end archive section -->"

SECTION = f"""{SECTION_START}
## The archive, and why it exists

The portal is a rolling window, not an archive. When FY2027 began on
1 October 2026 the contracts search stopped returning 1,297 of the 7,165
option-period rows in `raw/`: every period of 647 contracts, and the oldest
periods of 635 contracts it still lists. Payments in the current table begin on
6 October 2016.

`archive/` keeps every row ever pulled. Each file is the portal's response with
four fields added and nothing else changed:

| Field | Meaning |
|---|---|
| `listed` | Whether the latest pull returned the row. Absent on rows last pulled before the archive began; treat absent as listed. |
| `first_listed` | First pull that returned it, from October 2026 on. |
| `last_listed` | Last pull that returned it. |
| `recovered_on` | Set on rows lost on 1 October 2026 and put back: from `raw/` (the 2026-09-05 pull) where it held them, otherwise fetched from the portal's detail endpoint, which still answers for delisted contracts. Every period fetched that way that `raw/` also holds matched it field for field. |

`archive/parquet/` holds the same rows as Parquet, one file per table, for the
explorer at https://dc-procurement.pages.dev and for anyone who wants to query
them directly.
Amounts and dates are parsed from the portal's text, and each parsed column sits
beside the text it came from; `columns.json` says where every column comes from.

`contract_details_older_periods.json` holds periods the detail endpoint still
serves but the search had stopped listing before this project's first pull.
They are kept apart so that `contract_details.json` stays what the search
listed.

Payment `id`s repeat (879,641 rows, 865,562 ids in October 2026), so do not
de-duplicate on `id`. A payment is kept as delisted only when its `id` stops
appearing at all; when the portal revises a payment, the new rows replace the
old ones rather than sitting beside them.
{SECTION_END}"""


def _card(current: str) -> str:
    """The dataset card with the archive row and section in place, once."""
    card = current
    if TABLE_ROW not in card:
        card = re.sub(
            r"(\| `raw_baseline_20260824/` \|[^\n]*\n)", lambda m: m.group(1) + TABLE_ROW + "\n", card, count=1
        )
        if TABLE_ROW not in card:
            raise SystemExit("could not find the snapshot table in the dataset card; not publishing")
    pattern = re.compile(re.escape(SECTION_START) + r".*?" + re.escape(SECTION_END), re.S)
    if pattern.search(card):
        return pattern.sub(lambda _: SECTION, card)
    marker = "## Quirks worth knowing before you use this"
    if marker not in card:
        raise SystemExit("could not find where to put the archive section; not publishing")
    return card.replace(marker, SECTION + "\n\n" + marker, 1)


def publish(raw_dir: Path = RAW_DIR, repo_id: str = REPO_ID) -> str:
    """Upload the archive files and refresh the dataset card in one commit."""
    from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download

    _load_dotenv()
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set (environment or .env); nothing published")

    missing = [f for f in FILES if not (raw_dir / f).exists()]
    if missing:
        raise SystemExit(f"missing from {raw_dir}: {missing}; not publishing")

    api = HfApi(token=token)
    # These files only ever gain rows. Fewer rows than the published archive
    # means the run started without it, and publishing would throw it away.
    # Rows, not bytes: a restore that replaced rebuilt rows with the shorter
    # real ones made two files smaller while every row was still there.
    try:
        published = json.loads(
            Path(
                hf_hub_download(repo_id, "archive/manifest.json", repo_type="dataset", token=token)
            ).read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001 - no archive yet, nothing to protect
        published = {"snapshots": []}
    published_rows = {e["name"]: e["rows"] for e in published.get("snapshots", [])}
    shrunk = []
    for name in GROWS_ONLY:
        stem = name.removesuffix(".json")
        if stem in published_rows:
            rows = len(json.loads((raw_dir / name).read_text(encoding="utf-8")))
            if rows < published_rows[stem]:
                shrunk.append(f"{stem}: {rows:,} rows < {published_rows[stem]:,} published")
    if shrunk:
        raise SystemExit(f"not publishing: fewer rows than the published archive: {shrunk}")
    current = Path(hf_hub_download(repo_id, "README.md", repo_type="dataset", token=token)).read_text(
        encoding="utf-8"
    )
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    operations = [
        CommitOperationAdd(path_in_repo=f"archive/{name}", path_or_fileobj=str(raw_dir / name))
        for name in FILES
    ]
    operations.append(
        CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=_card(current).encode("utf-8"))
    )
    commit = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=operations,
        commit_message=f"Archive: portal pull of {manifest.get('pulled_on', 'unknown date')}",
    )
    return str(commit.commit_url)


def _local_matches(path: Path, remote: Any) -> bool:
    """Whether a local file is byte-identical to the published one.

    Large files are stored by sha256 (``lfs``); small ones by git blob id.
    """
    import hashlib

    data = path.read_bytes()
    if getattr(remote, "lfs", None):
        return hashlib.sha256(data).hexdigest() == remote.lfs.sha256
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return blob == remote.blob_id


def hydrate(raw_dir: Path = RAW_DIR, repo_id: str = REPO_ID) -> list[str]:
    """Download the archive into ``raw_dir`` for any file not already there.

    The weekly run merges each pull into the previous files, so after
    :func:`prune` has removed them they have to come back first. A file that is
    present is left alone, local copy wins.
    """
    from huggingface_hub import hf_hub_download

    import shutil
    from tempfile import TemporaryDirectory

    _load_dotenv()
    fetched = []
    raw_dir.mkdir(parents=True, exist_ok=True)
    # Downloaded into a local directory, not the hub cache: a cached copy would
    # keep the ~350MB that prune exists to free.
    with TemporaryDirectory(dir=raw_dir.parent) as tmp:
        for name in FILES:
            if (raw_dir / name).exists():
                continue
            path = hf_hub_download(
                repo_id,
                f"archive/{name}",
                repo_type="dataset",
                token=os.environ.get("HF_TOKEN"),
                local_dir=tmp,
            )
            (raw_dir / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(path, raw_dir / name)
            fetched.append(name)
    return fetched


def prune(raw_dir: Path = RAW_DIR, repo_id: str = REPO_ID) -> list[str]:
    """Delete local archive files, but only if every one is published unchanged.

    All-or-nothing: if any file is missing from the dataset or differs from it,
    nothing is deleted and the run says which.
    """
    from huggingface_hub import HfApi

    _load_dotenv()
    api = HfApi(token=os.environ.get("HF_TOKEN"))
    local = [name for name in FILES if (raw_dir / name).exists()]
    remote = {
        info.path: info
        for info in api.get_paths_info(
            repo_id, [f"archive/{n}" for n in local], repo_type="dataset", expand=True
        )
    }
    bad = [
        n for n in local
        if f"archive/{n}" not in remote or not _local_matches(raw_dir / n, remote[f"archive/{n}"])
    ]
    if bad:
        raise SystemExit(f"not deleting anything: these differ from or are missing on {repo_id}: {bad}")
    for name in local:
        (raw_dir / name).unlink()
    return local
