#!/usr/bin/env python3
"""Check the H-1B findings against DOL's own LCA disclosure files.

``analysis/pull_h1b.py`` reads h1bdata.info, a third-party mirror. This script
goes to the record: DOL OFLC's quarterly LCA disclosure files (Performance
Data page), FY2023 through the latest published quarter. Each quarterly file
holds the applications DECIDED in that quarter (~86-250MB, ~130k+ rows, 96
columns), including three fields the mirror drops that change what can be
claimed:

- ``CASE_STATUS`` -- the mirror counts filings; only Certified ones matter.
- ``WORKSITE_ADDRESS1/2`` -- a street address, not just a city.
- ``SECONDARY_ENTITY_BUSINESS_NAME`` -- when the worker is placed at a client
  site, the employer often names the client. A filing that names a DC agency
  here is direct documentation of a DC-government placement, a tier above any
  inference from worksite city.

Downloads: dol.gov sits behind Akamai bot protection that 403s non-browser
clients (curl/httpx fail on TLS fingerprint, not headers). The script tries
httpx first and, on 403, tells you to fetch the file with a real browser into
``data/h1b/dol/raw/`` -- the Performance Data page links every quarter:
https://www.dol.gov/agencies/eta/foreign-labor/performance

Each raw workbook is filtered immediately -- rows whose EMPLOYER_NAME matches
the vendor watchlist, or whose JOB_TITLE contains CURAM (any employer) -- and
only the filtered rows are kept on disk (``data/h1b/dol/lca_dol_filtered.csv``);
the raw file is deleted after filtering unless ``--keep-raw``. The report then
answers two questions: does any filing name a DC agency as the secondary
entity, and do the mirror's counts survive a certified-only cut.

    uv run --with python-calamine python analysis/pull_h1b_dol.py
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "h1b" / "dol" / "raw"
OUT = ROOT / "data" / "h1b" / "dol" / "lca_dol_filtered.csv"
MIRROR = ROOT / "data" / "h1b" / "lca_filings.csv"
BASE = "https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/"

# FY2023 through the latest published quarter. FY2023-FY2025 quarterly files
# each hold one quarter of decisions; the current-FY file (FY2026_Q3, served
# from /media/) is cumulative -- its decisions span Oct 2025 through Jun 2026,
# so Q1/Q2 are not fetched separately. Extend as DOL publishes.
QUARTERS = [f"FY{fy}_Q{q}" for fy in (2023, 2024, 2025) for q in (1, 2, 3, 4)] \
    + ["FY2026_Q3"]

# EMPLOYER_NAME patterns: the checked vendor set, including the zero-filing
# vendors (a zero against the primary source is also a finding).
VENDOR_RE = re.compile(
    "|".join([
        r"\bD2SOL", r"\bESYSTEMS", r"AHU TECHNOLOGIES", r"S3CLOUD",
        r"\bSERIGOR", r"\bCSZNET", r"PYRAMID CONSULTING", r"^COMPUTER AID",
        r"ADVANCED NETWORK CONSULTING", r"CHANGING TECHNOLOGIES",
        r"DISTRICT CONSULTANT", r"\bV3GATE", r"\bKARDER", r"AVID SYSTEMS",
        r"\bLIMBIC", r"\bSUPRETECH", r"\bKOVAIS", r"\bSYSMEGA",
        r"\bTECKNOMIC", r"STOCKBRIDGE CONSULTING", r"\bOBVERSE",
        r"INFOSYS PUBLIC SERVICES", r"^DIONA", r"^MAXIMUS (INC|TEK)",
        r"DENOVO SYSTEMS", r"NETLINE TECHNOLOGIES", r"OOTB MINDS",
        r"^MSYS INC",
    ]),
    re.IGNORECASE,
)

KEEP = [
    "CASE_NUMBER", "CASE_STATUS", "RECEIVED_DATE", "DECISION_DATE",
    "VISA_CLASS", "JOB_TITLE", "SOC_TITLE", "FULL_TIME_POSITION",
    "EMPLOYER_NAME", "EMPLOYER_CITY", "EMPLOYER_STATE",
    "WORKSITE_ADDRESS1", "WORKSITE_ADDRESS2", "WORKSITE_CITY",
    "WORKSITE_STATE", "WORKSITE_POSTAL_CODE",
    "WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_TO", "WAGE_UNIT_OF_PAY",
    "PREVAILING_WAGE", "PW_UNIT_OF_PAY", "PW_WAGE_LEVEL",
    "TOTAL_WORKER_POSITIONS", "NEW_EMPLOYMENT", "CONTINUED_EMPLOYMENT",
    "SECONDARY_ENTITY", "SECONDARY_ENTITY_BUSINESS_NAME",
    "TOTAL_WORKSITE_LOCATIONS", "APPENDIX_A_ATTACHED",
    "H_1B_DEPENDENT", "WILLFUL_VIOLATOR",
]


def try_download(name: str) -> bool:
    import httpx
    url = BASE + f"LCA_Disclosure_Data_{name}.xlsx"
    try:
        with httpx.stream("GET", url, timeout=600, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                print(f"  {name}: HTTP {r.status_code} -- dol.gov blocks "
                      "non-browser clients; download in a browser into "
                      f"{RAW}/", file=sys.stderr)
                return False
            with open(RAW / f"LCA_Disclosure_Data_{name}.xlsx", "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  {name}: download failed ({e})", file=sys.stderr)
        return False


def filter_one(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, engine="calamine", dtype=str)
    emp = df["EMPLOYER_NAME"].fillna("")
    title = df["JOB_TITLE"].fillna("")
    mask = emp.str.contains(VENDOR_RE) | title.str.contains("CURAM", case=False)
    cols = [c for c in KEEP if c in df.columns]
    out = df.loc[mask, cols].copy()
    out["source_file"] = path.name
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-raw", action="store_true",
                    help="keep raw workbooks after filtering (default: delete)")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)

    frames = []
    done = set()
    if OUT.exists():
        prev = pd.read_csv(OUT, dtype=str)
        frames.append(prev)
        done = set(prev["source_file"].unique())

    for name in QUARTERS:
        fname = f"LCA_Disclosure_Data_{name}.xlsx"
        path = RAW / fname
        if fname in done:
            continue
        if not path.exists() and not try_download(name):
            continue
        print(f"filtering {fname} ...", file=sys.stderr)
        out = filter_one(path)
        print(f"  kept {len(out)} rows", file=sys.stderr)
        frames.append(out)
        if not args.keep_raw:
            path.unlink()

    if not frames:
        sys.exit("no data: no filtered CSV and no raw files could be fetched")
    allrows = pd.concat(frames, ignore_index=True)
    allrows.to_csv(OUT, index=False)
    print(f"wrote {len(allrows)} rows -> {OUT}\n")

    # -- report ------------------------------------------------------------
    dc = allrows[allrows.WORKSITE_STATE.fillna("").str.upper().eq("DC")]
    sec = allrows[allrows.SECONDARY_ENTITY_BUSINESS_NAME.fillna("").str.strip()
                  .ne("")]
    print("Secondary-entity names on watchlist filings (verbatim):")
    grp = sec.groupby(["EMPLOYER_NAME", "SECONDARY_ENTITY_BUSINESS_NAME"])
    for (empn, entity), g in grp:
        cities = ", ".join(sorted(set(g.WORKSITE_CITY.fillna("?"))))
        print(f"  {empn} -> {entity!r}  (n={len(g)}, worksites: {cities})")
    if sec.empty:
        print("  (none)")

    print("\nCertified vs total, DC-worksite filings, by employer:")
    for empn, g in dc.groupby("EMPLOYER_NAME"):
        cert = int((g.CASE_STATUS == "Certified").sum())
        print(f"  {empn:<45}{len(g):>5} filed  {cert:>5} certified")

    if MIRROR.exists():
        mirror = pd.read_csv(MIRROR, dtype=str)
        print("\nMirror (submit year >= 2023) vs DOL (FY2023+ decisions), "
              "all worksites -- windows differ, exact equality not expected:")
        pats = {"esystems": "ESYSTEMS",
                "avid_systems": "AVID SYSTEMS", "csznet": "CSZNET",
                "s3cloudsolutions": "S3CLOUD", "serigor": "SERIGOR",
                "ahu_technologies": "AHU TECHNOLOGIES",
                "diona": "DIONA", "denovo_systems": "DENOVO SYSTEMS"}
        for slug, pat in pats.items():
            m = mirror[(mirror["query"] == slug)
                       & (mirror.submit_date.str[-4:] >= "2023")]
            d = allrows[allrows.EMPLOYER_NAME.str.upper()
                        .str.contains(pat, na=False)]
            dcert = int((d.CASE_STATUS == "Certified").sum())
            print(f"  {slug:<22} mirror {len(m):>4}   dol {len(d):>4} filed "
                  f"/ {dcert:>4} certified")
        # D2Sol has no standalone mirror query; compare Curam-titled rows.
        m = mirror[(mirror["query"] == "job_curam_all")
                   & mirror.employer.str.upper().str.startswith("D2SOL")
                   & (mirror.submit_date.str[-4:] >= "2023")]
        d = allrows[allrows.EMPLOYER_NAME.str.upper().str.contains("D2SOL", na=False)
                    & allrows.JOB_TITLE.str.contains("CURAM", case=False, na=False)]
        dcert = int((d.CASE_STATUS == "Certified").sum())
        print(f"  {'d2sol (curam only)':<22} mirror {len(m):>4}   dol {len(d):>4} filed "
              f"/ {dcert:>4} certified")


if __name__ == "__main__":
    main()
