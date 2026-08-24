#!/usr/bin/env python3
"""Pull H-1B LCA filings for DC's technology vendors and flag DC worksites.

D2Sol, holder of the District's $84M DCAS operations contract, files H-1B
Labor Condition Applications for Curam developers at DC worksites. This script
asks the same question about the rest of the ecosystem: every subvendor whose
job ads we captured (``evidence/job_ads``), every prime staffing vendor with a
DC contract, and the DCAS cast -- plus one job-title query for "CURAM" across
all employers, which is the sharpest instrument here, because DCAS is the
District's only Curam system and so a Curam title at a Washington DC worksite
is near-certainly the DCAS program.

Source: h1bdata.info, a third-party aggregator of DOL LCA disclosure data.
The authoritative source is DOL OFLC's LCA disclosure files; verify any number
there before publishing it. LCAs are not visas and not hires -- one worker can
generate several filings, filings can be withdrawn or denied, and a worksite
address never names the client. Employer search is substring-based, so each
vendor carries an ``employer_re`` filter that keeps only the intended legal
names (the bare query for "STOCKBRIDGE" returns an unrelated San Francisco
capital firm, for example).

Claim discipline: a DC worksite alone does not prove a DC-government
engagement. The tiers used in the analysis are (a) STRONG -- the vendor's
visible business is substantially DC government AND it files at DC worksites,
or the title itself is dispositive (Curam in DC); (b) MEDIUM -- DC contract
holder with DC-metro filings; (c) WEAK -- filings with no DC tie. This script
reports counts and rows; the tier call stays human.

Raw HTML is cached in ``data/h1b/raw/`` (one file per query, URL and fetch
date in the first two lines) and re-fetched only if missing, so re-runs are
free. Parsed rows land in ``data/h1b/lca_filings.csv``; a per-vendor summary
and the DC-worksite Curam breakdown print to stdout.

    uv run python analysis/pull_h1b.py            # use cache, fetch only missing
    uv run python analysis/pull_h1b.py --refresh  # re-fetch everything
"""

import argparse
import collections
import html
import re
import statistics
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import httpx

RAW = Path("data/h1b/raw")
OUT = Path("data/h1b/lca_filings.csv")
BASE = "https://h1bdata.info/index.php"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# slug, query ({"em": ...} or {"job": ...}), regex keeping only the intended
# employers (None = keep all). Zero-filing vendors stay listed: the zero is a
# finding (DC primes staffing without their own LCA filings).
VENDORS = [
    # Subvendor staffing firms whose captured job ads are DC-government reqs.
    ("ahu_technologies", {"em": "AHU TECHNOLOGIES"}, None),
    ("s3cloudsolutions", {"em": "S3CLOUD"}, None),
    ("collaboredge", {"em": "COLLABOREDGE"}, None),
    ("serigor", {"em": "SERIGOR"}, None),
    ("csznet", {"em": "CSZNET"}, None),
    ("pyramid_consulting", {"em": "PYRAMID CONSULTING"}, r"^PYRAMID CONSULTING"),
    # Prime staffing / IT vendors with DC contracts.
    ("computer_aid", {"em": "COMPUTER AID"}, r"^COMPUTER AID INC"),
    ("changing_technologies", {"em": "CHANGING TECHNOLOGIES"}, None),
    ("advanced_network_consulting", {"em": "ADVANCED NETWORK CONSULTING"}, None),
    ("district_consultant", {"em": "DISTRICT CONSULTANT"}, None),
    ("v3gate", {"em": "V3GATE"}, None),
    ("karder", {"em": "KARDER"}, None),
    ("avid_systems", {"em": "AVID SYSTEMS"}, None),
    ("limbic_systems", {"em": "LIMBIC"}, None),
    ("supretech", {"em": "SUPRETECH"}, None),
    ("kovais", {"em": "KOVAIS"}, None),
    ("sysmega", {"em": "SYSMEGA"}, None),
    ("tecknomic", {"em": "TECKNOMIC"}, None),
    ("stockbridge_consulting", {"em": "STOCKBRIDGE"}, r"CONSULTING"),
    ("obverse", {"em": "OBVERSE"}, None),
    # The DCAS cast.
    ("infosys_public_services", {"em": "INFOSYS PUBLIC SERVICES"}, None),
    ("diona", {"em": "DIONA"}, r"^DIONA \(?US\)?"),
    ("maximus", {"em": "MAXIMUS"}, r"^MAXIMUS (INC|TEK)"),
    # Curam-title filers surfaced by the job query below.
    ("esystems", {"em": "ESYSTEMS"}, r"^ESYSTEMS"),
    ("denovo_systems", {"em": "DENOVO"}, r"^DENOVO SYSTEMS"),
    # Every Curam-titled LCA nationwide, all employers. DCAS is DC's only
    # Curam system: a Curam title at a DC worksite is near-certainly DCAS.
    ("job_curam_all", {"job": "CURAM"}, None),
]

DC_METRO = [
    "WASHINGTON, DC", "WASHINGTON DC", "WASHINGTON, D.C",
    "ARLINGTON, VA", "ALEXANDRIA, VA", "FALLS CHURCH, VA", "MCLEAN, VA",
    "TYSONS", "VIENNA, VA", "FAIRFAX, VA", "ANNANDALE, VA", "SPRINGFIELD, VA",
    "RESTON, VA", "HERNDON, VA", "CHANTILLY, VA", "STERLING, VA",
    "ASHBURN, VA", "WOODBRIDGE, VA",
    "SILVER SPRING, MD", "BETHESDA, MD", "ROCKVILLE, MD", "GAITHERSBURG, MD",
    "GERMANTOWN, MD", "GREENBELT, MD", "LANHAM, MD", "LARGO, MD",
    "HYATTSVILLE, MD", "COLLEGE PARK, MD", "UPPER MARLBORO, MD",
    "CAPITOL HEIGHTS, MD", "BOWIE, MD", "LAUREL, MD", "COLUMBIA, MD",
]


def is_dc_city(loc: str) -> bool:
    return "WASHINGTON" in loc and ("DC" in loc or "D.C" in loc)


def is_dc_metro(loc: str) -> bool:
    return is_dc_city(loc) or any(m in loc for m in DC_METRO)


def query_url(params: dict) -> str:
    q = {"em": params.get("em", ""), "job": params.get("job", ""),
         "year": "All Years"}
    return f"{BASE}?{urllib.parse.urlencode(q)}"


def fetch(slug: str, params: dict, refresh: bool) -> str:
    path = RAW / f"{slug}.html"
    if path.exists() and not refresh:
        return path.read_text()
    url = query_url(params)
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=90,
                  follow_redirects=True)
    r.raise_for_status()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = f"<!-- URL: {url} -->\n<!-- Fetched: {stamp} -->\n" + r.text
    path.write_text(text)
    time.sleep(2)
    return text


def parse_rows(raw: str) -> list[list[str]]:
    """h1bdata rows: employer, job title, base salary, worksite, submit, start."""
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", raw, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 6 and cells[2].replace(",", "").isdigit():
            out.append(cells[:6])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch every page instead of using the cache")
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    all_rows = []
    summaries = []
    for slug, params, emp_re in VENDORS:
        try:
            raw = fetch(slug, params, args.refresh)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"{slug}: FETCH FAILED ({e})", file=sys.stderr)
            continue
        rows = parse_rows(raw)
        if emp_re:
            rows = [r for r in rows if re.search(emp_re, r[0])]
        for r in rows:
            all_rows.append([slug] + r)
        wages = sorted(int(r[2].replace(",", "")) for r in rows)
        dc = [r for r in rows if is_dc_city(r[3])]
        metro = [r for r in rows if is_dc_metro(r[3])]
        recent = [r for r in rows if r[4][-4:] >= "2023"]
        summaries.append((slug, len(rows), len(recent), len(dc), len(metro),
                          wages))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write("query,employer,job_title,base_salary,worksite,"
                "submit_date,start_date,dc_city,dc_metro\n")
        for row in all_rows:
            sal = row[3].replace(",", "")
            fields = [row[0], row[1], row[2], sal, row[4], row[5], row[6],
                      str(int(is_dc_city(row[4]))),
                      str(int(is_dc_metro(row[4])))]
            f.write(",".join('"' + x.replace('"', '""') + '"' if "," in x
                             else x for x in fields) + "\n")
    print(f"wrote {len(all_rows)} rows -> {OUT}\n")

    print(f"{'query':<28}{'filings':>8}{'2023+':>7}{'DC':>5}{'metro':>7}"
          f"{'wage min/med/max':>25}")
    for slug, n, recent, dc, metro, wages in summaries:
        w = (f"{wages[0]:,}/{int(statistics.median(wages)):,}/{wages[-1]:,}"
             if wages else "-")
        print(f"{slug:<28}{n:>8}{recent:>7}{dc:>5}{metro:>7}{w:>25}")

    curam = [r for r in all_rows if r[0] == "job_curam_all"]
    curam_dc = [r for r in curam if is_dc_city(r[4])]
    if curam:
        print(f"\nCuram-titled LCAs nationwide: {len(curam)}; "
              f"Washington DC worksite: {len(curam_dc)}")
        print("DC-worksite Curam filings by employer "
              "(near-certainly DCAS -- DC's only Curam system):")
        by_emp = collections.Counter(r[1] for r in curam_dc)
        for emp, n in by_emp.most_common():
            yrs = sorted({r[5][-4:] for r in curam_dc if r[1] == emp})
            print(f"  {emp:<45}{n:>4}  years {','.join(yrs)}")


if __name__ == "__main__":
    main()
