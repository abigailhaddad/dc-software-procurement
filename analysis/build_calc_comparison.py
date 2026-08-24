"""Compare the District's published rates against federal awarded ceilings.

    uv run python analysis/build_calc_comparison.py

Runs after pull_calc.py. Writes data/calc/dc_vs_calc_comparison.csv, which
verify_claims.py checks, plus the list of DC categories deliberately left
unmatched. data/calc/METHODS.md records why each pairing was accepted.
"""
import json
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CALC_DIR = ROOT / "data" / "calc"
BASE = "https://api.gsa.gov/acquisition/calc/v3/api/ceilingrates/?search=labor_category:"

# DC exact title -> (federal category title, raw file slug, match quality)
MATCHES = {
    "Computer Scientist":        ("Computer Scientist", "computer_scientist", "exact"),
    "Web Developer":             ("Web Developer", "web_developer", "exact"),
    "Software Engineer":         ("Software Engineer", "software_engineer", "exact"),
    "Systems Engineer":          ("Systems Engineer", "systems_engineer", "exact"),
    "Network Engineer/Specialist": ("Network Engineer", "network_engineer", "near-exact (DC title 'Network Engineer/Specialist')"),
    "Cyber Security Engineer":   ("Cyber Security Engineer", "cyber_security_engineer", "exact"),
    "Cyber Security Specialist": ("Cyber Security Specialist", "cyber_security_specialist", "exact"),
    "Enterprise Architect":      ("Enterprise Architect", "enterprise_architect", "exact"),
    "Help Desk Specialist":      ("Help Desk Specialist", "help_desk_specialist", "exact"),
    "Customer Support":          ("Customer Support Specialist", "customer_support_specialist", "near-exact (no exact federal 'Customer Support'; n=5)"),
    "Technical Writer":          ("Technical Writer", "technical_writer", "exact"),
    "Project Manager":           ("Project Manager", "project_manager", "exact"),
    "Program Manager":           ("Program Manager", "program_manager", "exact"),
    "Business Analyst":          ("Business Analyst", "business_analyst", "exact"),
}

# Experience bands mapped to DC Supply Schedule levels (analytic assumption, documented)
BANDS = {
    "Entry":      (0, 2),
    "Journeyman": (3, 5),
    "Senior":     (6, 9),
    "Master":     (10, 45),
}
MIN_N = 10  # minimum sample to report banded percentiles

def stats(prices):
    a = np.array(prices, dtype=float)
    if len(a) == 0:
        return None
    return dict(n=len(a), median=round(float(np.median(a)),2),
                p25=round(float(np.percentile(a,25)),2), p75=round(float(np.percentile(a,75)),2))

fed = {}
for dc_title,(fcat, slug, quality) in MATCHES.items():
    rows = json.load(open(CALC_DIR / "raw" / f"{slug}_all.json"))
    prices_all = [r["current_price"] for r in rows if r.get("current_price")]
    fed[fcat] = {"all": stats(prices_all), "bands": {}, "rows": rows}
    for band,(lo,hi) in BANDS.items():
        p = [r["current_price"] for r in rows
             if r.get("current_price") and r.get("min_years_experience") is not None
             and lo <= r["min_years_experience"] <= hi]
        fed[fcat]["bands"][band] = stats(p)

# DC side
df = pd.read_csv(ROOT / "data" / "labor_rates.csv")
df = df[df.pairing.isin(["deterministic","proximate"])]
dc = df[df.labor_category.isin(MATCHES.keys())].copy()

out = []
for _, r in dc.iterrows():
    fcat, slug, quality = MATCHES[r.labor_category]
    f = fed[fcat]
    level = r.level if pd.notna(r.level) else ""
    band = f["bands"].get(level)
    use_band = band is not None and band["n"] >= MIN_N
    s = band if use_band else f["all"]
    if use_band:
        lo,hi = BANDS[level]
        filt = f"client-side min_years_experience {lo}-{hi} (mapped to DC level '{level}')"
    else:
        filt = "none (all experience levels)" + (f"; banded n={band['n']}<{MIN_N} too small" if band is not None and level in BANDS else "")
    qurl = BASE + fcat.replace(" ","%20") + "&page=1&page_size=200"
    out.append(dict(
        dc_contract=r.contract_number, dc_vendor=r.vendor,
        dc_category=r.labor_category, dc_level=level, dc_rate=r.hourly_rate,
        federal_category_matched=fcat, match_quality=quality,
        federal_median=s["median"] if s else "", federal_p25=s["p25"] if s else "",
        federal_p75=s["p75"] if s else "", federal_n=s["n"] if s else 0,
        experience_filter=filt,
        federal_median_allexp=f["all"]["median"], federal_p25_allexp=f["all"]["p25"],
        federal_p75_allexp=f["all"]["p75"], federal_n_allexp=f["all"]["n"],
        dc_vs_fed_median_pct=round(100*(r.hourly_rate - s["median"])/s["median"],1) if s else "",
        source_query=qurl,
    ))

outdf = pd.DataFrame(out).sort_values(["dc_category","dc_level","dc_contract"])
outdf.to_csv(CALC_DIR / "dc_vs_calc_comparison.csv", index=False)
print("rows:", len(outdf))

# federal stats summary table
print("\nFederal distributions (current_price, active MAS contracts, all experience/education/worksites):")
for dc_title,(fcat, slug, q) in MATCHES.items():
    a = fed[fcat]["all"]
    bands_str = " | ".join(f"{b}: med {v['median']} (n={v['n']})" if v else f"{b}: -" for b,v in fed[fcat]["bands"].items())
    print(f"  {fcat}: n={a['n']} p25={a['p25']} med={a['median']} p75={a['p75']}   [{bands_str}]")

# DC categories in the same families that were left unmatched: numbered and
# suffixed variants ("Project Manager III", "Sr. Technical Writer") whose level
# cannot be read off the title. Anything outside the fourteen families is not a
# near-miss and is not listed. NEAR_MISSES catches variants that name their
# family; HAND_ADDED holds the two that are family variants without saying so.
HAND_ADDED = {
    "Geographic Information System (GIS) Analyst/Programmer",
    "System Architect (Software/Data/Database) I",
}
HEADER = """DC labor categories in the target families that were NOT matched to a federal CALC+ category
(conservative policy: only exact or near-exact plain-title matches were used).
Rates for these rows are excluded from the comparison CSV.
"""

unmatched = set(df.labor_category.dropna().unique()) - set(MATCHES)
near = sorted(
    t for t in unmatched
    if t in HAND_ADDED or any(k.lower() in str(t).lower() for k in MATCHES)
)
with open(CALC_DIR / "dc_categories_not_matched.txt","w") as fh:
    fh.write(HEADER + "\n" + "\n".join(near) + "\n")
print(f"\nDC categories NOT matched (conservative policy): {len(near)} family variants "
      f"of {len(unmatched)} unmatched titles, written to dc_categories_not_matched.txt")
