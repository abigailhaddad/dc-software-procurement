#!/usr/bin/env python3
"""List current contracts a reviewer new to the District would want to ask about.

Two kinds of lead, and neither is a finding:

* How it was awarded. The paperwork names a sole-source, emergency or letter
  contract D&F, or an extension, though ``market_type`` says "Open" or
  "Set Aside" on every one of them (see analysis/competition.py for why that
  field cannot answer the question). Filenames are free text and wrong in both
  directions, so a name hit only nominates a contract; the document text decides.
* What it is. Awarded in the ordinary way, but something about the record does
  not add up: an amount far out of line with the title, a title that is still
  the procurement system's placeholder, payments well past the authorised
  amount, the same award listed twice. A model also reads each record and says
  in plain words what is being bought and what, if anything, looks odd.

"Current" means an option period DC's site still lists that runs through the
day of the run. Rules are computed on the exported tables in data/raw/parquet
(``make hydrate export`` first). The model's answers are cached in
data/review_leads/llm_cache.json, which is tracked because it cost model calls.

    uv run python analysis/review_leads.py            # rules + model pass
    uv run python analysis/review_leads.py --no-llm   # rules only

Writes data/review_leads/leads.csv: one row per current contract that any rule
or the model flagged, with every signal as its own column and links to DC's
site. The model is not reproducible (see dcprocure.classify), so it answers
three times and the row carries how many of the three called it odd.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dcprocure.classify import _load_dotenv  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "raw" / "parquet"
OUT_DIR = ROOT / "data" / "review_leads"
CACHE_PATH = OUT_DIR / "llm_cache.json"

MODEL = "gpt-5.4-mini"
DRAWS = 3

log = logging.getLogger(__name__)

# Filename patterns. Loose on purpose ("Sole Soure", "Letter- Contract",
# "D and F" all occur); a hit only nominates the contract for a read.
NAME_PATTERNS = {
    "name_sole_source": r"sole[\s_-]*sou?r?c?e?|single[\s_-]*(available[\s_-]*)?source",
    "name_emergency": r"emergency[\s_-]*(procurement|contract|d\s*&?\s*f|multi)|d\s*&?\s*f[^/]*emergency",
    "name_letter_contract": r"letter[\s_-]*contract",
    "name_extension": r"extension|extend",
}

PLACEHOLDER_TITLE = r"untitled|contract workspace"


def load(con: duckdb.DuckDBPyConnection) -> None:
    for table in ("contracts", "documents", "contract_payments", "nigp_classes"):
        con.execute(f"create table {table} as select * from read_parquet('{PARQUET / table}.parquet')")
    con.execute(
        """create table cur as select * from contracts
           where listed and start_date <= current_date and end_date >= current_date"""
    )


def rule_signals(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    name_cols = ",\n".join(
        f"bool_or(regexp_matches(lower(name), '{pattern}')) as {col}"
        for col, pattern in NAME_PATTERNS.items()
    )
    con.execute(f"create table name_hits as select contract_number, {name_cols} from documents group by 1")

    # A contract can have two periods running today (3 do at the time of
    # writing); keep the later-starting one so each contract is one row.
    return con.sql(
        rf"""
        with cur1 as (
            select * from cur
            qualify row_number() over (partition by contract_number order by start_date desc) = 1
        ),
        periods as (
            select contract_number, count(*) as periods_listed, min(start_date) as first_start,
                   sum(amount) as authorised_all_periods
            from contracts group by 1
        ),
        jumps as (
            select contract_number, max(amount / prev) as max_period_jump
            from (select contract_number, amount,
                         lag(amount) over (partition by contract_number order by start_date) as prev
                  from contracts where amount > 0)
            where prev > 0 and amount >= 3 * prev and amount - prev >= 1e6
            group by 1
        ),
        paid as (
            select contract_number, sum(voucher_amount) as paid_all_time
            from contract_payments group by 1
        ),
        -- Same vendor, title and amount under more than one contract number.
        -- Case and punctuation are folded, and only the start of the vendor
        -- name is compared: the same DDOT award is listed once as
        -- "FORT MYER CONSTRUCTION CORPORA" (cut at 30 characters) and once as
        -- "Fort Myer Construction Corporation".
        dupkey as (
            select contract_number,
                   left(lower(regexp_replace(vendor, '[^A-Za-z0-9]', '', 'g')), 12) as v,
                   lower(regexp_replace(title, '[^A-Za-z0-9]', '', 'g')) as t, amount
            from cur1
        ),
        dups as (
            select a.contract_number, string_agg(b.contract_number, '; ' order by b.contract_number) as same_award_listed_as
            from dupkey a join dupkey b on a.v = b.v and a.t = b.t and a.amount = b.amount
                 and a.contract_number <> b.contract_number
            group by 1
        )
        select c.contract_number, c.agency, c.vendor, c.title, c.option_period, c.start_date, c.end_date,
               c.amount, c.contract_type, c.market_type, c.nigp_codes, c.portal_url,
               p.periods_listed, p.first_start, p.authorised_all_periods,
               coalesce(n.name_sole_source, false) as name_sole_source,
               coalesce(n.name_emergency, false) as name_emergency,
               coalesce(n.name_letter_contract, false) as name_letter_contract,
               coalesce(n.name_extension, false) as name_extension,
               j.max_period_jump,
               pd.paid_all_time,
               d.same_award_listed_as,
               regexp_matches(lower(c.title), '{PLACEHOLDER_TITLE}') as placeholder_title
        from cur1 c
        join periods p using (contract_number)
        left join name_hits n using (contract_number)
        left join jumps j using (contract_number)
        left join paid pd using (contract_number)
        left join dups d using (contract_number)
        """
    ).df()


def _vendor_key(vendor: str | None) -> str:
    # "TEMPLE GROUP" and "THE TEMPLE GROUP INC" are the same vendor on two records.
    return re.sub(r"[^a-z]", "", re.sub(r"^\s*the\s+", "", (vendor or "").lower()))[:8]


def named_elsewhere(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Contracts whose number appears in another record's title or document filenames.

    DC's site lists some DDOT awards twice: once under the DCKA number the
    contract itself carries, with the payments and no documents, and once under
    a CW number, with the documents and no payments. The CW record names the
    DCKA number in its title or its award filename ("DCKA-2024-C-0008 - Citywide
    Thermoplastic...", "Executed Signed_22-C-0072.pdf"). Titles and amounts
    often differ between the two, so the title-and-amount rule misses them.

    Short forms ("22-C-0072") are matched, so the vendor must match too: the
    CFSA numbers DCRL-2023-C-0035 and DDOT's DCKA-2023-C-0035 share a short form.
    """
    records = con.sql("select contract_number, any_value(vendor) vendor, any_value(title) title from contracts group by 1").df()
    docs = con.sql("select distinct contract_number, name from documents").df()
    vendor = dict(zip(records.contract_number, records.vendor.map(_vendor_key)))
    texts = pd.concat(
        [records[["contract_number", "title"]].rename(columns={"title": "text"}),
         docs.rename(columns={"name": "text"})]
    ).dropna()
    found = []
    for cn in records.contract_number:
        m = re.fullmatch(r"([A-Z]{2,5})-20(\d\d)-([A-Z])-(\d{4})", cn)
        if not m:
            continue
        prefix, yy, kind, num = m.groups()
        pat = re.compile(rf"(?<![A-Za-z0-9])(?:{prefix}[-_ ]?)?(?:20)?{yy}[-_ ]?{kind}[-_ ]?{num}(?![-_ ]?\d)", re.I)
        hits = texts[(texts.contract_number != cn) & texts.text.str.contains(pat)]
        hits = hits[hits.contract_number.map(vendor) == vendor.get(cn)]
        for other in hits.contract_number.unique():
            found += [(cn, other), (other, cn)]
    return (
        pd.DataFrame(found, columns=["contract_number", "other"]).drop_duplicates()
        .groupby("contract_number").other.agg(lambda s: "; ".join(sorted(s))).rename("named_in").reset_index()
    )


def add_rule_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Paid more than every listed period authorised. Payments carrying a
    # contract number are a small share of all payments, and periods DC's site
    # stopped listing before October 2026 are not in the archive, so this can
    # miss overpayment and can also overstate it; it is a question to ask.
    df["paid_over_authorised"] = (df.paid_all_time > 1.1 * df.authorised_all_periods) & (
        df.paid_all_time - df.authorised_all_periods >= 250_000
    )
    df["period_amount_jump"] = df.max_period_jump.notna()
    df["listed_twice"] = df.same_award_listed_as.notna()
    rule_cols = [
        *NAME_PATTERNS,
        "placeholder_title",
        "paid_over_authorised",
        "period_amount_jump",
        "listed_twice",
    ]
    df["rule_flags"] = df[rule_cols].apply(lambda r: "; ".join(c for c in rule_cols if r[c]), axis=1)
    return df


# --- model pass -------------------------------------------------------------


class Read(BaseModel):
    what_it_buys: str = Field(description="One plain sentence: what the District is buying, from whom, for which agency.")
    odd: Literal["no", "maybe", "yes"] = Field(
        description="Would a careful reviewer new to the District stop and ask about this record?"
    )
    why: list[str] = Field(
        description="Each specific thing that looks off, tied to a field shown. Empty when odd is 'no'."
    )


SYSTEM = """You help a reviewer who is new to the District of Columbia government read its contract records.
For each record, say in one plain sentence what is being bought, then judge whether a careful reviewer would stop and ask about it.

Things worth asking about:
- an amount far out of line with what the title describes, or with the length of the period
- an agency buying something with no evident connection to its mission
- a vendor whose name suggests a different line of business from the title
- a title so vague or generic that the record does not say what is being bought
- something unusual for a government to buy at all

Not worth asking about on their own:
- large amounts for programs that are large by nature: Medicaid managed care, employee health insurance, citywide software agreements, construction, school transportation
- awards split among several vendors for the same service
- a title written in internal shorthand, if the rest of the record makes the purchase clear
- a fiscal year in the title older than the period shown: titles keep the year the contract was first awarded
- "DCSS" in the title: that is the DC Supply Schedule, a pre-approved vendor list any agency can order from, so these records name no agency and a broad category is expected
- a vendor name that sounds unrelated to the work: small resellers and supply-schedule holders often have such names. Ask only when the mismatch is stark, such as a hair salon providing IT systems, or the vendor and title naming different companies

Judge only from the fields shown. Do not guess at motives or wrongdoing. If the record is ordinary, say odd = "no" and leave why empty."""


def render(row: pd.Series) -> str:
    # Commodity classes are left out on purpose. They are self-assigned and
    # often wrong (see dcprocure.classify), and on the first pass most of what
    # the model flagged was a class that did not match the title.
    days = (row.end_date - row.start_date).days + 1
    agency = row.agency if isinstance(row.agency, str) else "(none listed)"
    return "\n".join(
        [
            f"Agency: {agency}",
            f"Vendor: {row.vendor}",
            f"Title: {row.title}",
            f"Contract type: {row.contract_type}",
            f"Amount authorised for this period: ${row.amount:,.2f}",
            f"Period: {row.option_period}, {row.start_date:%Y-%m-%d} to {row.end_date:%Y-%m-%d} ({days} days)",
        ]
    )


def _key(text: str, draw: int) -> str:
    return hashlib.sha256(f"{MODEL}|{SYSTEM}|{draw}|{text}".encode()).hexdigest()


async def _ask(text: str, sem: asyncio.Semaphore) -> dict | None:
    import litellm

    litellm.drop_params = True
    litellm.suppress_debug_info = True
    async with sem:
        for attempt in range(3):
            try:
                resp = await litellm.acompletion(
                    model=MODEL,
                    messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                    response_format=Read,
                    temperature=0.0,
                    timeout=120,
                )
                return Read(**json.loads(resp.choices[0].message.content)).model_dump()
            except Exception as exc:
                if attempt == 2:
                    log.warning("model call failed: %s", exc)
                    return None
                await asyncio.sleep(2**attempt)
    return None


async def model_pass(df: pd.DataFrame) -> pd.DataFrame:
    cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    texts = {row.contract_number: render(row) for row in df.itertuples()}
    todo = [(cn, d) for cn, t in texts.items() for d in range(DRAWS) if _key(t, d) not in cache]
    log.info("model pass: %d contracts, %d calls not cached", len(texts), len(todo))
    sem = asyncio.Semaphore(16)
    answers = await asyncio.gather(*(_ask(texts[cn], sem) for cn, _ in todo))
    for (cn, d), ans in zip(todo, answers):
        if ans is not None:
            cache[_key(texts[cn], d)] = ans
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True))

    rows = []
    for cn, t in texts.items():
        draws = [cache.get(_key(t, d)) for d in range(DRAWS)]
        got = [a for a in draws if a]
        odd_votes = sum(a["odd"] == "yes" for a in got)
        maybe_votes = sum(a["odd"] == "maybe" for a in got)
        # Report the reasons from the first draw that called it odd, so the
        # text shown matches a vote that was cast.
        first_odd = next((a for a in got if a["odd"] != "no"), got[0] if got else None)
        rows.append(
            {
                "contract_number": cn,
                "model_draws": len(got),
                "model_odd_votes": odd_votes,
                "model_maybe_votes": maybe_votes,
                "model_what_it_buys": first_odd["what_it_buys"] if first_odd else None,
                "model_why": " | ".join(first_odd["why"]) if first_odd else None,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    con = duckdb.connect()
    load(con)
    df = rule_signals(con)
    df = df.merge(named_elsewhere(con), on="contract_number", how="left")
    df["same_award_listed_as"] = [
        "; ".join(sorted({x for s in (a, b) if isinstance(s, str) for x in s.split("; ")})) or None
        for a, b in zip(df.same_award_listed_as, df.named_in)
    ]
    df = add_rule_flags(df.drop(columns="named_in"))
    log.info("current contracts: %d (as of %s)", len(df), date.today())

    if args.no_llm:
        df["model_odd_votes"] = 0
        df["model_maybe_votes"] = 0
    else:
        _load_dotenv()
        df = df.merge(asyncio.run(model_pass(df)), on="contract_number", how="left")

    flagged = df[(df.rule_flags != "") | (df.model_odd_votes >= 2)].copy()
    flagged["run_date"] = date.today().isoformat()
    flagged = flagged.sort_values(["model_odd_votes", "amount"], ascending=False)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    flagged.drop(columns=["nigp_codes"]).to_csv(OUT_DIR / "leads.csv", index=False)

    log.info("flagged: %d of %d", len(flagged), len(df))
    for col in [*NAME_PATTERNS, "placeholder_title", "paid_over_authorised", "period_amount_jump", "listed_twice"]:
        log.info("  %-26s %d", col, int(df[col].sum()))
    if not args.no_llm:
        log.info("  model odd (2+ of %d draws)  %d", DRAWS, int((df.model_odd_votes >= 2).sum()))
        log.info("  model split (1 of %d)        %d", DRAWS, int((df.model_odd_votes == 1).sum()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
