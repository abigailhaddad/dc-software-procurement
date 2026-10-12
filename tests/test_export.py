"""The explorer's tables hold the portal's rows, parsed correctly, and nothing else.

Every figure on the site is a count or a sum over these Parquet files, so these
are the checks behind every figure. They compare the export against the raw JSON
it was built from, through a second code path (``dcprocure.parse``, pandas)
rather than the SQL that built it.

Needs ``data/raw`` and the export: ``make hydrate export`` first. To check an
export written somewhere else, set ``DCPROCURE_PARQUET`` to its directory.
"""

import json
import os
import random
import re
from pathlib import Path

import duckdb
import pytest

from dcprocure import parse

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PARQUET = Path(os.environ.get("DCPROCURE_PARQUET") or RAW / "parquet")


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
        "solicitations": len(_raw("solicitations.json")),
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


def test_contract_link_tables_hold_what_the_full_tables_give(payments):
    # contract_payments: every payment line carrying a listed contract number,
    # recomputed from the raw JSON with pandas.
    listed = {r["contractNumber"] for r in _raw("contracts_by_commodity.json")}
    expected = payments[payments.contract_number.fillna("").str.strip().isin(listed)]
    lines, paid = _q(f"SELECT count(*), sum(voucher_amount) FROM '{PARQUET}/contract_payments.parquet'")[0]
    assert lines == len(expected)
    assert round(paid, 2) == round(expected.amount.sum(), 2)

    # contract_orders: the subquery contract.html ran over the full tables.
    for number, count in [("CW91684", 22), ("CW98098", None), ("CW95425", None)]:
        original = _q(f"""SELECT po_number, ordered_date, po_total_text FROM '{PARQUET}/purchase_orders.parquet'
            WHERE regexp_replace(po_number, '-V[0-9]+$', '') IN (
              SELECT DISTINCT regexp_replace(po_number, '-V[0-9]+$', '') FROM '{PARQUET}/payments.parquet'
              WHERE contract_number = '{number}' AND po_number IS NOT NULL)
            ORDER BY po_number""")
        linked = _q(f"""SELECT po_number, ordered_date, po_total_text FROM '{PARQUET}/contract_orders.parquet'
            WHERE contract_number = '{number}' ORDER BY po_number""")
        assert linked == original, number
        assert len(original) > 1, number
        if count is not None:
            assert len(linked) == count, number


def test_corrections_are_sourced_and_change_only_what_they_list():
    import yaml
    doc = yaml.safe_load((ROOT / "corrections.yaml").read_text(encoding="utf-8"))
    fixes = {(a["contract_number"], a["option_period"]): a for a in doc["amounts"]}
    assert all(a.get("source") for a in fixes.values())
    # DC's own amount is never overwritten; a correction sits beside it.
    rows = _q(f"SELECT contract_number, option_period, amount_text, corrected_amount FROM '{PARQUET}/contracts.parquet' "
              f"WHERE corrected_amount IS NOT NULL")
    for number, period, text, corrected in rows:
        a = fixes[(number, period)]
        assert text == a["published"] and corrected == float(a["amount"])
    # Every record named in a same-award group exists, and exactly one per group is counted.
    numbers = {r[0] for r in _q(f"SELECT DISTINCT contract_number FROM '{PARQUET}/contracts.parquet'")}
    flagged = dict(_q(f"SELECT DISTINCT contract_number, same_award_as FROM '{PARQUET}/contracts.parquet' "
                      f"WHERE same_award_as IS NOT NULL"))
    for g in doc["same_award"]:
        assert set(g["records"]) <= numbers, g["award"]
        assert g["count"] not in flagged
        assert all(flagged[r] == g["count"] for r in g["records"] if r != g["count"])


def test_totals_count_base_periods_once_with_corrections():
    # base_amount is the published base-period amount, corrected where listed, with
    # duplicates left out; option and unlabelled periods carry none.
    total, expected, stray = _q(f"""SELECT sum(base_amount),
        sum(coalesce(corrected_amount, amount)) FILTER (WHERE period_kind = 'base' AND same_award_as IS NULL),
        count(base_amount) FILTER (WHERE period_kind <> 'base' OR same_award_as IS NOT NULL)
        FROM '{PARQUET}/contracts.parquet'""")[0]
    assert round(total, 2) == round(expected, 2) and stray == 0
    kinds = dict(_q(f"SELECT option_period, any_value(period_kind) FROM '{PARQUET}/contracts.parquet' GROUP BY 1"))
    assert kinds["Base Period"] == "base" and kinds["Option 1"] == "option" and kinds["N/A"] == "unclear"


def _mdy(text):
    """M/D/YYYY -> date, parsed in Python rather than by the export's SQL."""
    import datetime
    if text is None:
        return None
    m, d, y = (int(x) for x in text.split()[0].split("/"))
    return datetime.date(y, m, d)


def test_every_solicitation_row_matches_the_raw_file():
    # Every raw row, field by field, matched as a multiset: nothing dropped,
    # merged or altered except the parsing and agency mapping COLUMNS states.
    import collections
    import yaml
    names = {k: v["name"] for k, v in yaml.safe_load((ROOT / "agency_names.yaml").read_text(encoding="utf-8"))["names"].items()}
    expected = collections.Counter()
    for r in _raw("solicitations.json"):
        agencies = [a for a in r["agencyNames"] if a is not None]
        sp = r["specialist"] or {}
        expected[(
            r["solicitationNumber"], r["title"].strip(" "),
            "; ".join(names.get(a, a) for a in agencies) or None, "; ".join(agencies) or None,
            r["marketType"], _mdy(r["closingDate"]), r["closingDate"], r["closingTime"],
            int(r["archive_fy"][2:]) if r["archive_fy"] else None, r["is_open"],
            sp.get("name"), sp.get("email"), sp.get("phone"),
        )] += 1
    got = collections.Counter(_q(f"""SELECT solicitation_number, title, agency, agency_as_published, market_type,
        closing_date, closing_date_text, closing_time, fiscal_year, is_open, specialist, specialist_email, specialist_phone
        FROM '{PARQUET}/solicitations.parquet'"""))
    assert got == expected


def test_solicitation_dates_and_years_are_consistent():
    raw = _raw("solicitations.json")
    # The only unparsed closing date is one DC's site leaves blank.
    blank = sum(1 for r in raw if not r["closingDate"])
    assert _q(f"SELECT count(*) FROM '{PARQUET}/solicitations.parquet' WHERE closing_date IS NULL")[0][0] == blank
    # A closed solicitation's archive year is the District fiscal year its closing date falls in.
    wrong = _q(f"""SELECT count(*) FROM '{PARQUET}/solicitations.parquet' WHERE NOT is_open
        AND fiscal_year <> year(closing_date) + CASE WHEN month(closing_date) >= 10 THEN 1 ELSE 0 END""")[0][0]
    assert wrong == 0
    # Open rows have no archive year; closed rows all do.
    assert _q(f"SELECT count(*) FROM '{PARQUET}/solicitations.parquet' WHERE is_open <> (fiscal_year IS NULL)")[0][0] == 0
    assert _q(f"SELECT count(*) FROM '{PARQUET}/solicitations.parquet' WHERE is_open")[0][0] == sum(r["is_open"] for r in raw)


def test_solicitation_summary_matches_the_raw_file():
    import collections
    raw = _raw("solicitations.json")
    s = json.loads((PARQUET / "summary.json").read_text(encoding="utf-8"))["solicitations"]
    numbers = collections.Counter(r["solicitationNumber"] for r in raw)
    assert s["solicitations"] == len(raw)
    assert s["numbers"] == len(numbers)
    assert s["repeated_numbers"] == sum(1 for v in numbers.values() if v > 1)
    assert s["doc_ids"] == sum(1 for r in raw if re.fullmatch(r"Doc[0-9]+", r["solicitationNumber"]))
    assert s["no_agency"] == sum(1 for r in raw if not [a for a in r["agencyNames"] if a is not None])
    assert s["open"] == sum(r["is_open"] for r in raw)


def test_unlisted_payment_numbers_split_text_from_numbers():
    # Recomputed from the raw JSON: an entry with no digit is typed text ("N/A"), not a number.
    listed = {c["contractNumber"] for c in _raw("contracts_by_commodity.json")}
    entries = [(p.get("contractNumber") or "").strip() for p in _raw("payments.json")]
    entries = [e for e in entries if e]
    text = [e for e in entries if not re.search(r"[0-9]", e)]
    numbers = {e for e in entries if re.search(r"[0-9]", e) and e not in listed}
    s = json.loads((PARQUET / "summary.json").read_text(encoding="utf-8"))["payments"]
    assert s["text_lines"] == len(text)
    assert s["text_entries"] == len(set(text) - listed)
    assert s["unlisted_numbers"] == len(numbers)
    assert s["unlisted_entries"] == s["unlisted_numbers"] + s["text_entries"]
