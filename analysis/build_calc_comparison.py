import json, csv
import pandas as pd
import numpy as np

CALC_DIR = "raw"
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
    rows = json.load(open(f"{CALC_DIR}/{slug}_all.json"))
    prices_all = [r["current_price"] for r in rows if r.get("current_price")]
    fed[fcat] = {"all": stats(prices_all), "bands": {}, "rows": rows}
    for band,(lo,hi) in BANDS.items():
        p = [r["current_price"] for r in rows
             if r.get("current_price") and r.get("min_years_experience") is not None
             and lo <= r["min_years_experience"] <= hi]
        fed[fcat]["bands"][band] = stats(p)

# DC side
df = pd.read_csv("/Users/abigailhaddad/Documents/repos/dcbudget/data/labor_rates.csv")
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
outdf.to_csv("dc_vs_calc_comparison.csv", index=False)
print("rows:", len(outdf))

# federal stats summary table
print("\nFederal distributions (current_price, active MAS contracts, all experience/education/worksites):")
for dc_title,(fcat, slug, q) in MATCHES.items():
    a = fed[fcat]["all"]
    bands_str = " | ".join(f"{b}: med {v['median']} (n={v['n']})" if v else f"{b}: -" for b,v in fed[fcat]["bands"].items())
    print(f"  {fcat}: n={a['n']} p25={a['p25']} med={a['median']} p75={a['p75']}   [{bands_str}]")

# DC unmatched categories (variants I did not conservatively match)
matched_lower = set(MATCHES.keys())
unmatched = sorted(set(df.labor_category.unique()) - matched_lower)
with open("dc_categories_not_matched.txt","w") as fh:
    fh.write("\n".join(str(u) for u in unmatched))
print("\nDC categories NOT matched (conservative policy):", len(unmatched), "written to dc_categories_not_matched.txt")
