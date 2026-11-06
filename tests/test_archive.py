"""A pull must never lose a row the portal has stopped returning."""

import json

import pytest

from dcprocure.archive import PullShrankError, contract_id, retain_by_group, retain_by_key


def test_contract_id_matches_portal():
    assert contract_id("CW130594", "Base Period") == "Q1cxMzA1OTTCpkJhc2UgUGVyaW9k"


def test_retain_by_key_keeps_delisted_and_refreshes_listed(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([{"id": "a", "v": 1, "first_listed": "2026-01-01"}, {"id": "b"}]))
    merged, retained = retain_by_key(
        path, [{"id": "a", "v": 2}, {"id": "c"}], lambda r: r["id"], today="2026-10-12"
    )
    by_id = {r["id"]: r for r in merged}
    assert retained == 1
    assert {k: r["listed"] for k, r in by_id.items()} == {"a": True, "b": False, "c": True}
    assert by_id["a"]["v"] == 2
    assert by_id["a"]["first_listed"] == "2026-01-01"
    assert by_id["c"]["first_listed"] == "2026-10-12"


def test_retain_by_group_replaces_revised_payments_and_keeps_vanished_ones(tmp_path):
    # Payment ids repeat, and a payment can come back revised under its id.
    old = [{"id": "1", "amt": 5}, {"id": "1", "amt": 5}, {"id": "2", "amt": 6}, {"id": "3", "amt": 9}]
    path = tmp_path / "payments.json"
    path.write_text(json.dumps(old))
    current = [{"id": "1", "amt": 5}, {"id": "1", "amt": 5}, {"id": "2", "amt": 60}]
    merged, retained = retain_by_group(path, current, lambda r: r["id"])

    # The revised payment 2 appears once, at its new amount: not double counted.
    assert sorted((r["id"], r["amt"]) for r in merged) == [("1", 5), ("1", 5), ("2", 60), ("3", 9)]
    assert retained == 1
    assert [r["listed"] for r in merged if r["id"] == "3"] == [False]

    # Re-running with the same pull neither grows nor loses.
    path.write_text(json.dumps(merged))
    again, _ = retain_by_group(path, current, lambda r: r["id"])
    assert len(again) == 4


def test_split_to_corpus_restores_only_what_the_analysis_had():
    from dcprocure.archive import split_to_corpus

    def period(n, end, amount):
        return {"id": f"X{n}", "contractNumber": "CW1", "endDate": end, "contractAmount": amount}

    found = [period(0, "9/30/2018", "$10.00"), period(1, "9/30/2019", "$20.00"), period(2, "9/30/2020", "$30.00")]
    corpus, older = split_to_corpus(found, {"CW1": (2, 50.0)})
    assert [r["id"] for r in corpus] == ["X1", "X2"]
    assert [r["id"] for r in older] == ["X0"]

    # Latest periods that do not sum to the recorded ceiling are not guessed at.
    corpus, older = split_to_corpus(found, {"CW1": (1, 10.0)})
    assert corpus == [] and len(older) == 3


def test_split_to_corpus_fills_dropped_periods_of_a_listed_contract():
    from dcprocure.archive import split_to_corpus

    def period(n, end, amount):
        return {"id": f"X{n}", "contractNumber": "CW1", "endDate": end, "contractAmount": amount}

    listed = [period(3, "9/30/2021", "$40.00")]
    found = [period(0, "9/30/2018", "$10.00"), period(1, "9/30/2019", "$20.00"), period(2, "9/30/2020", "$30.00")]
    # The analysis had X2 and X3 (ceiling 70); X3 is still listed.
    corpus, older = split_to_corpus(found, {"CW1": (2, 70.0)}, {"CW1": listed})
    assert [r["id"] for r in corpus] == ["X2"]
    assert sorted(r["id"] for r in older) == ["X0", "X1"]


def test_restore_from_snapshot_adds_only_missing_periods(tmp_path):
    from dcprocure.archive import restore_from_snapshot

    raw, snap = tmp_path / "raw", tmp_path / "snap"
    raw.mkdir()
    snap.mkdir()
    a = {"id": "A", "contractNumber": "CW1"}
    b = {"id": "B", "contractNumber": "CW1"}
    (snap / "contract_details.json").write_text(json.dumps([a, b]))
    (snap / "contracts_by_commodity.json").write_text(
        json.dumps([{**a, "matched_classes": ["920"]}, {**b, "matched_classes": ["208"]}])
    )
    (raw / "contract_details.json").write_text(json.dumps([a]))
    (raw / "contracts_by_commodity.json").write_text(json.dumps([{**a, "matched_classes": ["920"]}]))
    (raw / "contract_details_older_periods.json").write_text(json.dumps([b]))

    result = restore_from_snapshot(raw, snap)
    details = json.loads((raw / "contract_details.json").read_text())
    search = json.loads((raw / "contracts_by_commodity.json").read_text())
    assert result["periods_restored"] == 1
    assert [d["id"] for d in details] == ["A", "B"] and details[1]["listed"] is False
    assert search[1]["matched_classes"] == ["208"]  # the snapshot row, whole
    # Restored, so no longer set aside.
    assert json.loads((raw / "contract_details_older_periods.json").read_text()) == []


def test_purchase_order_revisions_are_one_order():
    from dcprocure.pull import _po_base

    assert _po_base("PO736374-V4") == _po_base("PO736374-V5") == _po_base("PO736374") == "PO736374"


def test_solicitation_years_run_through_the_current_fiscal_year():
    from datetime import date

    from dcprocure.pull import solicitation_years

    assert solicitation_years(date(2026, 9, 30))[-1] == "FY2026"
    assert solicitation_years(date(2026, 10, 1))[-1] == "FY2027"
    assert solicitation_years(date(2026, 10, 1))[0] == "FY2013"


def _write_rows(path, n, **extra):
    path.write_text(json.dumps([{"id": str(i), **extra} for i in range(n)]))


def test_a_pull_that_drops_most_of_the_archive_fails_and_writes_nothing(tmp_path):
    path = tmp_path / "rows.json"
    _write_rows(path, 1000)
    before = path.read_text()
    current = [{"id": str(i)} for i in range(100)]  # 90% missing: a truncated response
    with pytest.raises(PullShrankError, match="900 of the 1,000"):
        retain_by_key(path, current, lambda r: r["id"])
    assert path.read_text() == before


def test_an_empty_pull_fails_for_groups_too(tmp_path):
    path = tmp_path / "payments.json"
    _write_rows(path, 500)
    with pytest.raises(PullShrankError):
        retain_by_group(path, [], lambda r: r["id"])


def test_the_october_2026_drop_is_not_a_failure(tmp_path):
    # 1,297 of 7,165 periods (18%) left when the fiscal year turned.
    path = tmp_path / "rows.json"
    _write_rows(path, 7165)
    current = [{"id": str(i)} for i in range(7165 - 1297)]
    merged, retained = retain_by_key(path, current, lambda r: r["id"])
    assert retained == 1297 and len(merged) == 7165


def test_rows_already_delisted_do_not_count_toward_the_limit(tmp_path):
    path = tmp_path / "rows.json"
    rows = [{"id": str(i), "listed": False} for i in range(900)] + [{"id": f"k{i}"} for i in range(300)]
    path.write_text(json.dumps(rows))
    current = [{"id": f"k{i}"} for i in range(200)]  # 100 of the 300 listed rows go: a third
    merged, retained = retain_by_key(path, current, lambda r: r["id"])
    assert retained == 1000 and len(merged) == 1200


def test_the_limit_can_be_overridden_for_a_pull_that_really_shrank(tmp_path, monkeypatch):
    path = tmp_path / "rows.json"
    _write_rows(path, 1000)
    monkeypatch.setenv("DCPROCURE_ALLOW_SHRINK", "1")
    merged, retained = retain_by_key(path, [{"id": "0"}], lambda r: r["id"])
    assert retained == 999


def test_a_small_archive_is_not_checked(tmp_path):
    path = tmp_path / "rows.json"
    _write_rows(path, 50)
    merged, retained = retain_by_key(path, [{"id": "0"}], lambda r: r["id"])
    assert retained == 49
