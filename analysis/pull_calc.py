import json, time, urllib.request, urllib.parse, os

BASE = "https://api.gsa.gov/acquisition/calc/v3/api/ceilingrates/"
CATS = ["Computer Scientist","Web Developer","Software Engineer","Systems Engineer",
        "Network Engineer","Cyber Security Engineer","Cyber Security Specialist",
        "Enterprise Architect","Help Desk Specialist","Customer Support",
        "Technical Writer","Project Manager","Program Manager","Business Analyst"]

os.makedirs("raw", exist_ok=True)
manifest = {}
for cat in CATS:
    slug = cat.lower().replace(" ","_").replace("/","-")
    all_hits, page, total = [], 1, None
    while True:
        q = urllib.parse.urlencode({"search": f"labor_category:{cat}", "page": page, "page_size": 200})
        url = f"{BASE}?{q}"
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        with open(f"raw/{slug}_p{page}.json","w") as f:
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
    with open(f"raw/{slug}_all.json","w") as f:
        json.dump(rows, f)
    print(f"{cat}: total={total} fetched={len(all_hits)}")
with open("raw/manifest.json","w") as f:
    json.dump(manifest, f, indent=2)
