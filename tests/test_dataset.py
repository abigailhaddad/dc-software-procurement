"""The public dataset build and upload, on tiny synthetic tables. No network."""

import json
from types import SimpleNamespace

import duckdb
import pytest

from dcprocure import dataset


def _make_source(tmp_path, *, summary_periods=2, id_type="VARCHAR"):
    src = tmp_path / "src"
    src.mkdir()
    con = duckdb.connect()

    def write(name, sql):
        con.execute(f"COPY ({sql}) TO '{src / (name + '.parquet')}' (FORMAT PARQUET)")

    write("contracts", f"""SELECT * FROM (VALUES
        (CAST('007' AS {id_type}), 'CW1', DATE '2021-01-01', DATE '2020-12-15', DATE '2026-11-02', true, 'note', 'detail', NULL::DATE),
        (CAST('008' AS {id_type}), 'CW2', DATE '2022-01-01', DATE '2021-12-15', DATE '2026-11-02', false, NULL, NULL, DATE '2026-10-20')
        ) t(id, contract_number, start_date, award_date, last_listed, listed, of_interest, of_interest_detail, recovered_on)""")
    # Payment ids repeat: two lines share id 'p1'.
    write("payments", """SELECT * FROM (VALUES
        ('p1', 'CW1', DATE '2026-01-05', 10.0, 40.0, true),
        ('p1', 'CW1', DATE '2026-01-05', 30.0, 40.0, true),
        ('p2', NULL, DATE '2026-02-05', 5.0, 5.0, false)
        ) t(id, contract_number, payment_date, voucher_amount, check_total, listed)""")
    write("purchase_orders", """SELECT * FROM (VALUES
        ('PO00123', DATE '2026-03-01', true), ('PO00124-V2', DATE '2026-03-02', false)
        ) t(po_number, ordered_date, listed)""")
    write("solicitations", """SELECT * FROM (VALUES
        ('S1', DATE '2026-12-01', 'a@dc.gov', '202-555-0100'), ('S2', DATE '2026-12-02', 'b@dc.gov', ' ')
        ) t(solicitation_number, closing_date, specialist_email, specialist_phone)""")
    write("documents", "SELECT * FROM (VALUES ('CW1', 'd1'), ('CW1', 'd2')) t(contract_number, document_id)")
    write("nigp_classes", "SELECT * FROM (VALUES ('918', 'consulting')) t(nigp_class, description)")
    (src / "summary.json").write_text(json.dumps({
        "contracts": {"periods": summary_periods, "contracts": 2},
        "payments": {"lines": 3, "ids": 2, "no_contract": 1, "paid": 45.0, "check_totals": 85.0},
        "purchase_orders": {"orders": 2},
        "solicitations": {"solicitations": 2},
    }))
    (src / "columns.json").write_text(json.dumps({
        t: {} for t in ("contracts", "payments", "purchase_orders", "solicitations", "documents", "nigp_classes")
    }))
    return src


def test_build_writes_tables_and_a_card_and_passes_every_check(tmp_path):
    out = tmp_path / "out"
    report = dataset.build(_make_source(tmp_path), out)
    assert report["tables"]["payments"]["rows"] == 3  # repeats kept, not de-duplicated
    assert report["as_of"] == "2026-11-02"
    assert all(report["tables"][t]["bytes"] > 0 for t in dataset.TABLES)
    card = (out / "README.md").read_text()
    assert "2026-11-02" in card and "unofficial copy" in card and "check" in card.lower()
    assert "license: other" in card  # no license is asserted for the data


def test_dropped_columns_are_not_published(tmp_path):
    out = tmp_path / "out"
    dataset.build(_make_source(tmp_path), out)
    con = duckdb.connect()
    contracts = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{out / 'data' / 'contracts.parquet'}'").fetchall()}
    solicitations = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{out / 'data' / 'solicitations.parquet'}'").fetchall()}
    assert not contracts & {"of_interest", "of_interest_detail"}
    assert not solicitations & {"specialist_email", "specialist_phone"}
    assert {"id", "contract_number", "recovered_on"} <= contracts


def test_ids_keep_their_leading_zeros(tmp_path):
    out = tmp_path / "out"
    dataset.build(_make_source(tmp_path), out)
    ids = duckdb.connect().execute(f"SELECT id FROM '{out / 'data' / 'contracts.parquet'}' ORDER BY id").fetchall()
    assert ids == [("007",), ("008",)]


def test_a_count_that_disagrees_with_the_summary_stops_the_build(tmp_path):
    with pytest.raises(dataset.PublishError, match="summary.json says 5"):
        dataset.build(_make_source(tmp_path, summary_periods=5), tmp_path / "out")


def test_identifier_columns_that_are_not_text_stop_the_build(tmp_path):
    with pytest.raises(dataset.PublishError, match="not text"):
        dataset.build(_make_source(tmp_path, id_type="INTEGER"), tmp_path / "out")


def test_a_drop_list_naming_a_missing_column_stops_the_build(tmp_path, monkeypatch):
    monkeypatch.setitem(dataset.DROP_COLUMNS, "payments", ("not_a_column",))
    with pytest.raises(dataset.PublishError, match="not_a_column"):
        dataset.build(_make_source(tmp_path), tmp_path / "out")


class _FakeApi:
    """Records what upload asks Hugging Face to do."""

    def __init__(self, exists=True, stale=()):
        self.exists, self.stale = exists, stale
        self.created = None
        self.commits = []

    def dataset_info(self, repo_id):
        if not self.exists:
            from huggingface_hub.errors import RepositoryNotFoundError

            raise RepositoryNotFoundError("missing", response=SimpleNamespace(status_code=404, headers={}, request=None))
        return SimpleNamespace(sha="abc123", siblings=[SimpleNamespace(rfilename=f) for f in self.stale])

    def create_repo(self, repo_id, repo_type, private):
        self.created = (repo_id, repo_type, private)

    def create_commit(self, **kwargs):
        self.commits.append(kwargs)
        return SimpleNamespace(commit_url="https://huggingface.co/x/commit/1")


def _upload(tmp_path, monkeypatch, api):
    out = tmp_path / "out"
    dataset.build(_make_source(tmp_path), out)
    monkeypatch.setenv("HF_TOKEN", "test")
    monkeypatch.setattr("huggingface_hub.HfApi", lambda token=None: api)
    return dataset.upload(out, "someone/dc-procurement")


def test_upload_is_one_commit_and_creates_a_public_repo_when_missing(tmp_path, monkeypatch):
    api = _FakeApi(exists=False)
    _upload(tmp_path, monkeypatch, api)
    assert api.created == ("someone/dc-procurement", "dataset", False)
    assert len(api.commits) == 1
    paths = sorted(op.path_in_repo for op in api.commits[0]["operations"])
    assert paths == ["README.md"] + [f"data/{t}.parquet" for t in sorted(dataset.TABLES)]
    assert api.commits[0]["parent_commit"] is None


def test_upload_removes_stale_data_files_in_the_same_commit(tmp_path, monkeypatch):
    api = _FakeApi(stale=("data/old_table.parquet", "data/contracts.parquet", "README.md", "notes.txt"))
    _upload(tmp_path, monkeypatch, api)
    assert len(api.commits) == 1
    deletes = [op.path_in_repo for op in api.commits[0]["operations"] if type(op).__name__ == "CommitOperationDelete"]
    assert deletes == ["data/old_table.parquet"]  # not notes.txt, not files still wanted
    assert api.commits[0]["parent_commit"] == "abc123"
    assert api.created is None


def test_upload_without_a_token_does_nothing(tmp_path, monkeypatch):
    out = tmp_path / "out"
    dataset.build(_make_source(tmp_path), out)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(dataset, "_load_dotenv", lambda: None)
    with pytest.raises(SystemExit, match="HF_TOKEN"):
        dataset.upload(out, "someone/dc-procurement")
