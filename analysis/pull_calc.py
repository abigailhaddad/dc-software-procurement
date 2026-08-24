"""Pull GSA CALC ceiling rates into data/calc/raw/, one file per labor category.

    uv run python analysis/pull_calc.py

CALC publishes awarded ceiling rates on federal MAS contracts. These are the
comparison set for the District's own published rate cards; the raw API
responses are kept page by page so the pull can be audited.
"""
import json, time, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "calc" / "raw"

BASE = "https://api.gsa.gov/acquisition/calc/v3/api/ceilingrates/"
CATS = ["Computer Scientist","Web Developer","Software Engineer","Systems Engineer",
        "Network Engineer","Cyber Security Engineer","Cyber Security Specialist",
        "Enterprise Architect","Help Desk Specialist","Customer Support",
        "Technical Writer","Project Manager","Program Manager","Business Analyst"]

RAW.mkdir(parents=True, exist_ok=True)
manifest = {}
for cat in CATS:
    slug = cat.lower().replace(" ","_").replace("/","-")
    all_hits, page, total = [], 1, None
    while True:
        q = urllib.parse.urlencode({"search": f"labor_category:{cat}", "page": page, "page_size": 200})
        url = f"{BASE}?{q}"
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        with open(RAW / f"{slug}_p{page}.json","w") as f:
            json.dump(d, f)
        hits = d["hits"]["hits"]
        total = d["hits"]["total"]["value"]
        all_hits += hits
        if not hits or len(all_hits) >= total or page > 60:
            break
        page += 1
        time.sleep(0.3)
    manifest[cat] = {"total": total, "fetched": len(all_hits), "pages": page,
                     "first_query_url": f"{BASE}?search=labor_category:{urllib.parse.quote(cat)}&page=1&page_size=200"}
    # flatten
    rows = [h["_source"] for h in all_hits]
    with open(RAW / f"{slug}_all.json","w") as f:
        json.dump(rows, f)
    print(f"{cat}: total={total} fetched={len(all_hits)}")
with open(RAW / "manifest.json","w") as f:
    json.dump(manifest, f, indent=2)
