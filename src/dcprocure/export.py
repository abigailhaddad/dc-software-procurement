"""Write the archive as Parquet tables a browser can query.

The site is an explorer over these files, run in the browser by DuckDB. They
hold the portal's rows and nothing else: every column is a portal field, either
as published (the ``*_text`` columns) or parsed into a number or a date by a
rule stated in :data:`COLUMNS`. There are no classifications, joins across
tables, or derived categories here, so every figure the site shows is a query a
reader can see and rerun. The exception is two link tables, ``contract_payments``
and ``contract_orders``: copies of payments and purchase_orders rows cut down to
the listed contracts by the rule stated in :data:`COLUMNS`, so a contract's page
need not download the full tables.

The one thing added is the archive's own bookkeeping -- ``listed``,
``first_listed``, ``last_listed``, ``recovered_on`` -- which says whether the
portal still returns a row (see :mod:`dcprocure.archive`).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import yaml

from .pull import RAW_DIR

PORTAL = "https://contracts.ocp.dc.gov"
AGENCY_NAMES = Path(__file__).resolve().parents[2] / "agency_names.yaml"


def agency_map() -> dict[str, str]:
    """As-published agency value -> the name the site shows. See agency_names.yaml."""
    entries = yaml.safe_load(AGENCY_NAMES.read_text(encoding="utf-8"))["names"]
    for raw, entry in entries.items():
        if not entry.get("name") or not entry.get("source"):
            raise ValueError(f"agency_names.yaml: {raw!r} needs both a name and a source")
    return {raw: entry["name"] for raw, entry in entries.items()}

# Column, then where it comes from. The site prints this as its data dictionary.
COLUMNS: dict[str, dict[str, str]] = {
    "contracts": {
        "id": "Portal id of the option period (base64 of contract number and period name)",
        "contract_number": "contractNumber",
        "title": "title",
        "vendor": "vendor",
        "agency": "agencyNames, joined with '; ' when there are several, with abbreviated and variant names mapped by agency_names.yaml",
        "agency_as_published": "agencyNames exactly as published, joined with '; '",
        "option_period": "currentOptionPeriod, as typed by the District",
        "start_date": "startDate, parsed from M/D/YYYY",
        "end_date": "endDate, parsed from M/D/YYYY",
        "award_date": "awardDate, parsed from M/D/YYYY",
        "start_fiscal_year": "District fiscal year (1 October to 30 September) in which start_date falls",
        "amount": "contractAmount, parsed from text such as '$1,234.50'; the amount authorised for this option period",
        "amount_text": "contractAmount exactly as published",
        "nigp_codes": "commodityCodes on the search listing, as published",
        "nigp_classes": "First three digits of each code in nigp_codes",
        "contract_type": "contractType, from the contract's detail page",
        "market_type": "marketType, from the contract's detail page",
        "contracting_officer": "contractingOfficerName, from the contract's detail page",
        "documents": "Number of attachments listed on the detail page for this option period",
        "portal_url": "The District's detail page for this option period",
        "listed": "Whether the portal's search returned this row on the latest pull. A row it no longer returns comes from an earlier pull; its detail page may still open",
        "first_listed": "First pull that returned it (tracked from October 2026)",
        "last_listed": "Last pull that returned it",
        "recovered_on": "Set when the row was put back after the portal stopped listing it",
    },
    "documents": {
        "contract_number": "contractNumber of the option period the document is filed under",
        "contract_id": "Portal id of that option period",
        "document_id": "Portal id of the document",
        "name": "Document name as published",
        "category": "The category the portal writes into the name ('Contract Award', 'Modifications', 'D&Fs', ...); empty when the name does not follow the portal's pattern",
        "url": "Direct link to the PDF on the portal",
        "listed": "Whether the portal still lists the option period",
    },
    "purchase_orders": {
        "po_number": "poNumber; a revised order carries a -V suffix",
        "agency": "agencyName, with abbreviated and variant names mapped by agency_names.yaml",
        "agency_as_published": "agencyName exactly as published",
        "supplier": "supplierName",
        "nigp_code": "commodityCode",
        "nigp_class": "First three digits of nigp_code",
        "fiscal_year": "fiscalYear, as published (e.g. FY2026), as a number",
        "ordered_date": "orderedDate, parsed from M/D/YYYY",
        "po_total": "poTotal, parsed",
        "po_total_text": "poTotal exactly as published",
        "listed": "Whether the portal's search returned this row on the latest pull. A row it no longer returns comes from an earlier pull; its detail page may still open",
    },
    "payments": {
        "id": "Portal id of the payment line. Not unique: several lines can share one",
        "agency": "agencyName, with abbreviated and variant names mapped by agency_names.yaml",
        "agency_as_published": "agencyName exactly as published",
        "contract_number": "contractNumber; empty on most rows",
        "vendor": "vendorName, as published (often cut off at a fixed length)",
        "po_number": "poNumber",
        "voucher_number": "voucherNumber",
        "payment_date": "paymentDate, parsed from M/D/YYYY",
        "fiscal_year": "District fiscal year of payment_date (1 October to 30 September)",
        "voucher_amount": "voucherAmount, parsed; the amount of this line. Sum this one",
        "voucher_amount_text": "voucherAmount exactly as published",
        "check_total": "paymentAmount, parsed. NOT this line's amount: the total of the whole check or ACH run, repeated on every line in it. Do not sum",
        "payment_number": "paymentNumber, as published (masked by the portal)",
        "listed": "Whether the portal's search returned this row on the latest pull. A row it no longer returns comes from an earlier pull; its detail page may still open",
    },
    "nigp_classes": {
        "nigp_class": "Three-digit NIGP class",
        "description": "The portal's description of the class",
    },
}

# Two link tables repeat rows of payments and purchase_orders, cut down to the
# listed contracts, so one contract's page loads a small file instead of both
# full tables. Their columns are the source table's, described the same way.
COLUMNS["contract_payments"] = {
    "contract_number": "contractNumber. Every payments row whose contract_number is the contract_number of a row in contracts, "
                       "and no other; the columns are those of payments",
    **{k: v for k, v in COLUMNS["payments"].items() if k != "contract_number"},
}
COLUMNS["contract_orders"] = {
    "contract_number": "A contract_number in contracts. The order is linked to it through payments: some payment line carrying "
                       "this contract number has the order's po_number, once a trailing -V<digits> revision suffix is removed "
                       "from both. One row per contract and order; an order never paid under a contract number is not here",
    **COLUMNS["purchase_orders"],
}

_MONEY = """
    CASE WHEN {c} IS NULL OR trim({c}) = '' THEN NULL
         WHEN trim({c}) LIKE '(%' THEN -CAST(regexp_replace({c}, '[^0-9.]', '', 'g') AS DOUBLE)
         ELSE CAST(regexp_replace({c}, '[^0-9.-]', '', 'g') AS DOUBLE) END
"""


def _money(column: str) -> str:
    return _MONEY.format(c=column)


def _date(column: str) -> str:
    return f"TRY_STRPTIME(split_part({column}, ' ', 1), '%m/%d/%Y')::DATE"


def export(raw_dir: Path = RAW_DIR, out_dir: Path | None = None) -> dict[str, int]:
    """Write one Parquet file per table into ``out_dir``; return row counts."""
    out_dir = out_dir or raw_dir / "parquet"
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    def load(name: str, path: Path) -> None:
        # Read through Python so every field arrives as text, exactly as
        # published, whatever DuckDB's JSON type inference would guess.
        rows = json.loads(path.read_text(encoding="utf-8"))
        # The archive's bookkeeping fields only appear once a pull or a
        # restore has written them, so make sure the columns exist.
        keys = sorted({k for r in rows for k in r} | {"listed", "first_listed", "last_listed", "recovered_on"})
        flat = [
            {k: (json.dumps(r[k]) if isinstance(r.get(k), (list, dict)) else
                 (None if r.get(k) is None else str(r[k]))) for k in keys}
            for r in rows
        ]
        con.register(f"{name}_py", __import__("pandas").DataFrame(flat, columns=keys))
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM {name}_py")

    load("search", raw_dir / "contracts_by_commodity.json")
    load("details", raw_dir / "contract_details.json")
    load("po", raw_dir / "purchase_by_commodity.json")
    load("pay", raw_dir / "payments.json")
    load("classes", raw_dir / "commodities.json")
    names = agency_map()
    con.register("agency_names_py", __import__("pandas").DataFrame(
        {"raw": list(names), "mapped": list(names.values())}))
    con.execute("CREATE TABLE agency_names AS SELECT * FROM agency_names_py")

    con.execute(f"""
        CREATE TABLE contracts AS
        SELECT s.id,
               s.contractNumber AS contract_number,
               trim(s.title) AS title,
               s.vendor,
               array_to_string(list_transform(CAST(s.agencyNames AS VARCHAR[]),
                   a -> coalesce(list_extract(map_extract(names.m, a), 1), a)), '; ') AS agency,
               array_to_string(CAST(s.agencyNames AS VARCHAR[]), '; ') AS agency_as_published,
               s.currentOptionPeriod AS option_period,
               {_date('s.startDate')} AS start_date,
               {_date('s.endDate')} AS end_date,
               {_date('s.awardDate')} AS award_date,
               year({_date('s.startDate')}) + CASE WHEN month({_date('s.startDate')}) >= 10 THEN 1 ELSE 0 END AS start_fiscal_year,
               {_money('s.contractAmount')} AS amount,
               s.contractAmount AS amount_text,
               CAST(s.commodityCodes AS VARCHAR[]) AS nigp_codes,
               list_distinct(list_transform(CAST(s.commodityCodes AS VARCHAR[]), x -> left(trim(x), 3))) AS nigp_classes,
               d.contractType AS contract_type,
               d.marketType AS market_type,
               d.contractingOfficerName AS contracting_officer,
               coalesce(json_array_length(d.documents), 0) AS documents,
               '{PORTAL}/contracts/details?id=' || url_encode(s.id) AS portal_url,
               coalesce(s.listed, 'True') = 'True' AS listed,
               TRY_CAST(s.first_listed AS DATE) AS first_listed,
               TRY_CAST(s.last_listed AS DATE) AS last_listed,
               TRY_CAST(s.recovered_on AS DATE) AS recovered_on
        FROM search s LEFT JOIN details d USING (id)
        CROSS JOIN (SELECT map(list(raw), list(mapped)) AS m FROM agency_names) names
        ORDER BY contract_number, start_date
    """)
    con.execute(f"""
        CREATE TABLE documents AS
        WITH docs AS (
            SELECT d.contractNumber AS contract_number, d.id AS contract_id,
                   unnest(CAST(d.documents AS STRUCT(id VARCHAR, name VARCHAR)[])) AS doc
            FROM details d
        )
        SELECT docs.contract_number, docs.contract_id,
               doc.id AS document_id, doc.name AS name,
               CASE WHEN len(string_split(doc.name, '-')) >= 4
                    THEN trim(string_split(doc.name, '-')[3]) END AS category,
               '{PORTAL}/contracts/attachments/' || url_encode(doc.id) AS url,
               c.listed
        FROM docs JOIN contracts c ON c.id = docs.contract_id
        ORDER BY docs.contract_number, name
    """)
    con.execute(f"""
        CREATE TABLE purchase_orders AS
        SELECT poNumber AS po_number, coalesce(n.mapped, agencyName) AS agency, agencyName AS agency_as_published,
               supplierName AS supplier,
               commodityCode AS nigp_code, left(commodityCode, 3) AS nigp_class,
               TRY_CAST(replace(fiscalYear, 'FY', '') AS INTEGER) AS fiscal_year,
               {_date('orderedDate')} AS ordered_date,
               {_money('poTotal')} AS po_total, poTotal AS po_total_text,
               coalesce(listed, 'True') = 'True' AS listed
        FROM po LEFT JOIN agency_names n ON n.raw = po.agencyName ORDER BY ordered_date, po_number
    """)
    con.execute(f"""
        CREATE TABLE payments AS
        SELECT id, coalesce(n.mapped, agencyName) AS agency, agencyName AS agency_as_published,
               nullif(trim(contractNumber), '') AS contract_number,
               vendorName AS vendor, poNumber AS po_number, voucherNumber AS voucher_number,
               {_date('paymentDate')} AS payment_date,
               year({_date('paymentDate')}) + CASE WHEN month({_date('paymentDate')}) >= 10 THEN 1 ELSE 0 END AS fiscal_year,
               {_money('voucherAmount')} AS voucher_amount, voucherAmount AS voucher_amount_text,
               {_money('paymentAmount')} AS check_total,
               paymentNumber AS payment_number,
               coalesce(listed, 'True') = 'True' AS listed
        FROM pay LEFT JOIN agency_names n ON n.raw = pay.agencyName ORDER BY payment_date, id
    """)
    con.execute("CREATE TABLE nigp_classes AS SELECT code AS nigp_class, description FROM classes ORDER BY code")
    con.execute("""
        CREATE TABLE contract_payments AS
        SELECT * FROM payments
        WHERE contract_number IN (SELECT contract_number FROM contracts)
        ORDER BY contract_number, payment_date, id
    """)
    # An order carries no contract number; it is linked through the payment
    # lines that carry both. The payments table leaves off a revised order's
    # -V suffix, so it is dropped on both sides. Same rule as the ORDERS query
    # contract.html used before this table existed.
    con.execute("""
        CREATE TABLE contract_orders AS
        WITH links AS (
            SELECT DISTINCT contract_number, regexp_replace(po_number, '-V[0-9]+$', '') AS po_base
            FROM contract_payments WHERE po_number IS NOT NULL
        )
        SELECT l.contract_number, o.*
        FROM purchase_orders o JOIN links l ON l.po_base = regexp_replace(o.po_number, '-V[0-9]+$', '')
        ORDER BY l.contract_number, o.ordered_date, o.po_number
    """)

    counts = {}
    for table in COLUMNS:
        con.execute(
            f"COPY {table} TO '{out_dir / (table + '.parquet')}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        counts[table] = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    (out_dir / "columns.json").write_text(json.dumps(COLUMNS, indent=1), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary(con), indent=1, default=str), encoding="utf-8")
    return counts


def summary(con: duckdb.DuckDBPyConnection) -> dict:
    """The figures the About page states, computed from the tables just written.

    Written beside them every run, so the page can show them without loading
    the tables, and they can never describe an older pull than the tables do.
    """
    one = lambda sql: dict(zip([d[0] for d in con.execute(sql).description], con.execute(sql).fetchone()))
    names = yaml.safe_load(AGENCY_NAMES.read_text(encoding="utf-8"))["names"]
    mapped = con.execute("""
        SELECT agency_as_published, agency, sum(c) AS contracts, sum(o) AS orders, sum(p) AS payments FROM (
          SELECT unnest(string_split(agency_as_published, '; ')) AS agency_as_published,
                 unnest(string_split(agency, '; ')) AS agency, 1 AS c, 0 AS o, 0 AS p FROM contracts
          UNION ALL SELECT agency_as_published, agency, 0, 1, 0 FROM purchase_orders
          UNION ALL SELECT agency_as_published, agency, 0, 0, 1 FROM payments)
        WHERE agency IS DISTINCT FROM agency_as_published
        GROUP BY 1, 2 ORDER BY 1""").fetchall()
    return {
        "payments": one("""SELECT count(*) AS lines, count(DISTINCT id) AS ids,
            sum(voucher_amount) AS paid, sum(check_total) AS check_totals,
            count(*) FILTER (WHERE contract_number IS NULL) AS no_contract,
            sum(voucher_amount) FILTER (WHERE contract_number IS NULL) AS no_contract_paid,
            CAST(min(payment_date) AS VARCHAR) AS first_date, CAST(max(payment_date) AS VARCHAR) AS last_date,
            sum(voucher_amount) FILTER (WHERE contract_number IN (SELECT contract_number FROM contracts)) AS listed_contract_paid,
            count(DISTINCT contract_number) FILTER (WHERE contract_number NOT IN (SELECT contract_number FROM contracts)) AS unlisted_numbers,
            sum(voucher_amount) FILTER (WHERE contract_number NOT IN (SELECT contract_number FROM contracts)) AS unlisted_paid
            FROM payments"""),
        "contracts": one("""SELECT count(*) AS periods, count(DISTINCT contract_number) AS contracts,
            count(DISTINCT vendor) AS vendors, count(DISTINCT agency) AS agencies,
            count(DISTINCT market_type) AS market_types FROM contracts"""),
        "delisted": one("""SELECT (SELECT count(*) FROM contracts WHERE NOT listed) AS periods,
            (SELECT count(DISTINCT contract_number) FROM contracts WHERE NOT listed) AS contracts,
            (SELECT count(*) FROM purchase_orders WHERE NOT listed) AS orders,
            (SELECT count(*) FROM payments WHERE NOT listed) AS payments"""),
        "purchase_orders": one("SELECT count(*) AS orders, count(DISTINCT nigp_class) AS classes FROM purchase_orders"),
        "agency_names": [
            {"published": raw, "shown": shown, "source": names[raw]["source"],
             "contracts": c, "orders": o, "payments": p}
            for raw, shown, c, o, p in mapped
        ],
    }
