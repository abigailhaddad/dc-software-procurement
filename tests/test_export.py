"""The explorer's tables hold the portal's rows, parsed correctly, and nothing else.

Every figure on the site is a count or a sum over these Parquet files, so these
are the checks behind every figure. They compare the export against the raw JSON
it was built from, through a second code path (``dcprocure.parse``, pandas)
rather than the SQL that built it.

Needs ``data/raw`` and the export: ``make hydrate export`` first.
"""

import json
import random
import re
from pathlib import Path

import duckdb
import pytest

from dcprocure import parse

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PARQUET = RAW / "parquet"


def _q(sql: str):
    return duckdb.connect().execute(sql).fetchall()


def _raw(name: str) -> list[dict]:
    return json.loads((RAW / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def payments():
    return parse.load_payments()


def test_row_counts_match_the_raw_files():
    pairs = {
        "contracts": len(_raw("contracts_by_commodity.json")),
        "purchase_orders": len(_raw("purchase_by_commodity.json")),
        "payments": len(_raw("payments.json")),
        "nigp_classes": len(_raw("commodities.json")),
        "documents": sum(len(d.get("documents") or []) for d in _raw("contract_details.json")),
    }
    for table, expected in pairs.items():
        assert _q(f"SELECT count(*) FROM '{PARQUET / table}.parquet'")[0][0] == expected, table


def test_payment_totals_match_an_independent_parse(payments):
    lines, checks = _q(f"SELECT sum(voucher_amount), sum(check_total) FROM '{PARQUET}/payments.parquet'")[0]
    assert round(lines, 2) == round(payments.amount.sum(), 2)
    assert round(checks, 2) == round(payments.check_total.sum(), 2)


def test_no_published_value_is_lost_in_parsing():
    for table, number, text in [
        ("payments", "voucher_amount", "voucher_amount_text"),
        ("contracts", "amount", "amount_text"),
        ("purchase_orders", "po_total", "po_total_text"),
    ]:
        lost = _q(
            f"SELECT count(*) FROM '{PARQUET / table}.parquet' "
            f"WHERE {number} IS NULL AND nullif(trim({text}), '') IS NOT NULL"
        )[0][0]
        assert lost == 0, f"{table}.{number}: {lost} values published but not parsed"
    for table, column in [("payments", "payment_date"), ("contracts", "start_date"),
                          ("contracts", "end_date"), ("purchase_orders", "ordered_date")]:
        assert _q(f"SELECT count(*) FROM '{PARQUET / table}.parquet' WHERE {column} IS NULL")[0][0] == 0, column


def test_negative_amounts_in_parentheses_parse_as_negative():
    raw = [r for r in _raw("payments.json") if str(r.get("voucherAmount", "")).strip().startswith("(")]
    assert raw, "expected some refunds written in parentheses"
    sample = random.Random(0).sample(raw, min(20, len(raw)))
    for r in sample:
        got = _q(f"SELECT voucher_amount FROM '{PARQUET}/payments.parquet' "
                 f"WHERE id = '{r['id']}' AND voucher_amount_text = '{r['voucherAmount']}'")
        assert got and got[0][0] < 0, r


def test_a_random_sample_of_contract_rows_matches_the_portal_fields():
    raw = {r["id"]: r for r in _raw("contracts_by_commodity.json")}
    rows = _q(f"SELECT id, contract_number, vendor, amount_text, nigp_codes FROM '{PARQUET}/contracts.parquet'")
    for id_, number, vendor, amount, codes in random.Random(1).sample(rows, 300):
        o = raw[id_]
        assert (number, vendor, amount, list(codes)) == (
            o["contractNumber"], o["vendor"], o["contractAmount"], o["commodityCodes"])


def test_listed_flags_carry_through():
    raw_delisted = sum(1 for r in _raw("contracts_by_commodity.json") if r.get("listed") is False)
    assert _q(f"SELECT count(*) FROM '{PARQUET}/contracts.parquet' WHERE NOT listed")[0][0] == raw_delisted


def test_payment_fiscal_year_follows_the_district_calendar():
    wrong = _q(f"""SELECT count(*) FROM '{PARQUET}/payments.parquet'
                   WHERE fiscal_year <> year(payment_date) + CASE WHEN month(payment_date) >= 10 THEN 1 ELSE 0 END""")
    assert wrong[0][0] == 0
    edge = _q(f"SELECT DISTINCT fiscal_year FROM '{PARQUET}/payments.parquet' WHERE payment_date = DATE '2025-10-01'")
    assert edge in ([], [(2026,)])


def test_the_readme_states_no_figures_or_dates():
    # The figures live on the site, computed from the data; the README is about
    # the code. Digits allowed are identifiers: anything in a code span or
    # block, NIGP class codes, H-1B, and a contract title quoted verbatim.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    stripped = re.sub(r"```.*?```", "", readme, flags=re.S)
    stripped = re.sub(r"`[^`]*`", "", stripped)
    for token in ("918", "920", "961/962", "H-1B", "FY25 Nurse"):
        stripped = stripped.replace(token, "")
    assert re.findall(r"\S*\d\S*", stripped) == []


def test_agency_mapping_is_sourced_and_reversible():
    import yaml
    entries = yaml.safe_load((ROOT / "agency_names.yaml").read_text(encoding="utf-8"))["names"]
    assert all(e.get("name") and e.get("source") for e in entries.values())
    # Every changed value is one the file lists, and nothing else moved.
    for table in ("payments", "purchase_orders"):
        changed = _q(f"SELECT DISTINCT agency_as_published, agency FROM '{PARQUET / table}.parquet' "
                     f"WHERE agency IS DISTINCT FROM agency_as_published")
        assert all(entries[raw]["name"] == mapped for raw, mapped in changed), table
    # Two agencies share the abbreviation OPC; neither is mapped onto the other.
    opc = _q(f"SELECT DISTINCT agency FROM '{PARQUET}/payments.parquet' WHERE agency LIKE '%(OPC)'")
    assert sorted(r[0] for r in opc) == ["People's Counsel (OPC)", "Police Complaints (OPC)"]
