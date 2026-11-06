"""Spot-check the built public tables against the raw archive JSON, field by field.

A second path from the one that built them: this reads the portal's responses
(``contracts_by_commodity.json`` and friends, from data/raw or the archive
dataset's ``archive/`` folder) directly in Python and compares a sample of rows
with the Parquet tables. The sample is random plus edge cases (delisted rows,
recovered rows, amounts the portal gave as text, identifiers with leading zeros).

    uv run python scripts/spot_check_dataset.py RAW_DIR BUILT_DIR [--n 25] [--proof proof.csv]

Exits 1 if any field differs. ``--proof`` writes every compared value, raw next to
built, for review.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import duckdb


def _date(text):
    """The portal's M/D/YYYY text as an ISO date; None for blank."""
    text = (text or "").strip().split("T")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _s(value):
    return None if value is None or str(value).strip() == "" else str(value)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", type=Path)
    ap.add_argument("built", type=Path)
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--proof", type=Path)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    con = duckdb.connect()
    proof: list[dict] = []
    bad = 0

    def built_rows(table: str, where: str):
        path = str(args.built / "data" / f"{table}.parquet").replace("'", "''")
        cur = con.execute(f"SELECT * FROM '{path}' WHERE {where}")
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def compare(table, key, pairs):
        nonlocal bad
        for field, raw_value, built_value in pairs:
            ok = _s(raw_value) == _s(built_value)
            proof.append({"table": table, "key": key, "field": field, "raw": raw_value, "built": built_value, "match": ok})
            if not ok:
                bad += 1
                print(f"MISMATCH {table} {key} {field}: raw={raw_value!r} built={built_value!r}")

    def pick(rows, special):
        """A random sample plus rows that satisfy each edge-case test."""
        chosen = {id(r): r for r in rng.sample(rows, min(args.n, len(rows)))}
        for test in special:
            hits = [r for r in rows if test(r)]
            for r in rng.sample(hits, min(5, len(hits))):
                chosen[id(r)] = r
        return list(chosen.values())

    # contracts: one row per option period, keyed on id.
    raw = _load(args.raw / "contracts_by_commodity.json")
    sample = pick(raw, [lambda r: r.get("listed") is False, lambda r: not (r.get("contractAmount") or "").strip().startswith("$")])
    for r in sample:
        rows = built_rows("contracts", f"id = '{r['id']}'")
        if len(rows) != 1:
            bad += 1
            print(f"MISSING/DUPLICATE contracts id={r['id']}: {len(rows)} rows")
            continue
        b = rows[0]
        compare("contracts", r["id"], [
            ("contract_number", r.get("contractNumber"), b["contract_number"]),
            ("vendor", r.get("vendor"), b["vendor"]),
            ("title", r.get("title"), b["title"]),
            ("option_period", r.get("currentOptionPeriod"), b["option_period"]),
            ("amount_text", r.get("contractAmount"), b["amount_text"]),
            ("start_date", _date(r.get("startDate")), b["start_date"]),
            ("end_date", _date(r.get("endDate")), b["end_date"]),
            ("award_date", _date(r.get("awardDate")), b["award_date"]),
            ("agency_as_published", "; ".join(a for a in (r.get("agencyNames") or []) if a), b["agency_as_published"]),
            ("listed", r.get("listed", True), b["listed"]),
        ])

    # purchase orders: keyed on po_number.
    raw = _load(args.raw / "purchase_by_commodity.json")
    sample = pick(raw, [lambda r: r.get("listed") is False, lambda r: "-V" in r["poNumber"], lambda r: r["poNumber"][:1].isdigit()])
    for r in sample:
        rows = built_rows("purchase_orders", f"po_number = '{r['poNumber']}'")
        if len(rows) != 1:
            bad += 1
            print(f"MISSING/DUPLICATE purchase_orders po_number={r['poNumber']}: {len(rows)} rows")
            continue
        b = rows[0]
        compare("purchase_orders", r["poNumber"], [
            ("supplier", r.get("supplierName"), b["supplier"]),
            ("po_total_text", r.get("poTotal"), b["po_total_text"]),
            ("ordered_date", _date(r.get("orderedDate")), b["ordered_date"]),
            ("fiscal_year", str(r.get("fiscalYear") or "").upper().removeprefix("FY"), b["fiscal_year"]),
            ("agency_as_published", r.get("agencyName"), b["agency_as_published"]),
            ("listed", r.get("listed", True), b["listed"]),
        ])

    # payments: ids repeat, so compare every line of each sampled id, as a set.
    raw = _load(args.raw / "payments.json")
    by_id: dict[str, list[dict]] = {}
    for r in raw:
        by_id.setdefault(str(r["id"]), []).append(r)
    ids = list(by_id)
    chosen = set(rng.sample(ids, min(args.n, len(ids))))
    for test in (lambda g: len(g) > 1, lambda g: g[0].get("listed") is False, lambda g: bool(g[0].get("contractNumber"))):
        hits = [i for i in ids if test(by_id[i])]
        chosen |= set(rng.sample(hits, min(5, len(hits))))
    for i in sorted(chosen):
        def line(vendor, voucher, number, amount_text, date_iso, contract, po):
            return (_s(vendor), _s(voucher), _s(number), _s(amount_text), _s(date_iso), _s(contract), _s(po))
        want = sorted(
            line(r.get("vendorName"), r.get("voucherNumber"), r.get("paymentNumber"), r.get("voucherAmount"),
                 _date(r.get("paymentDate")), r.get("contractNumber"), r.get("poNumber")) for r in by_id[i]
        )
        got = sorted(
            line(b["vendor"], b["voucher_number"], b["payment_number"], b["voucher_amount_text"],
                 b["payment_date"], b["contract_number"], b["po_number"]) for b in built_rows("payments", f"id = '{i}'")
        )
        proof.append({"table": "payments", "key": i, "field": f"{len(want)} lines", "raw": want, "built": got, "match": want == got})
        if want != got:
            bad += 1
            print(f"MISMATCH payments id={i}:\n  raw  ={want}\n  built={got}")

    # solicitations: the number repeats once, so compare as sets per number.
    raw = _load(args.raw / "solicitations.json")
    sample = pick(raw, [lambda r: r.get("is_open") is True])
    for r in sample:
        n = r["solicitationNumber"]
        rows = built_rows("solicitations", f"solicitation_number = '{n.replace(chr(39), chr(39) * 2)}'")
        want = sorted(_s(x.get("title")) or "" for x in raw if x["solicitationNumber"] == n)
        got = sorted(_s(b["title"]) or "" for b in rows)
        proof.append({"table": "solicitations", "key": n, "field": "titles", "raw": want, "built": got, "match": want == got})
        if want != got:
            bad += 1
            print(f"MISMATCH solicitations {n}: raw={want} built={got}")
        if len(rows) == 1:
            b = rows[0]
            compare("solicitations", n, [
                ("closing_date", _date(r.get("closingDate")), b["closing_date"]),
                ("closing_date_text", r.get("closingDate"), b["closing_date_text"]),
                ("is_open", r.get("is_open"), b["is_open"]),
            ])

    checked = len(proof)
    print(f"{checked} comparisons, {bad} mismatches")
    if args.proof:
        with args.proof.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["table", "key", "field", "raw", "built", "match"])
            w.writeheader()
            w.writerows(proof)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
