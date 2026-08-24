"""Combine the two solicitation traces into one row per technology contract.

    uv run python analysis/solicitations_visibility.py

Runs after solicitations_pdfscan.py and solicitations_join.py, which supply the
two independent traces: what the award document cites, and what the contract
title matches. A contract counts as visible if either one lands on a
solicitation actually in the pull. verify_claims.py checks the totals.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "solicitations"

# A title match this close is treated as the same procurement. Below it the
# pairs stop being the same thing -- see the audit samples solicitations_join.py
# prints for the 0.5-0.85 band.
TITLE_MATCH_THRESHOLD = 0.80

# What the award document alone can tell you, when neither trace reaches a
# solicitation in the pull.
UNTRACED = {
    "ref_not_in_pull": "cites_number_not_in_pull",
    "schedule_ref_only": "schedule_vehicle_only",
    "explicit_no_solicitation": "award_says_no_solicitation",
    "no_ref_in_text": "no_ref_award_text",
    "no_award_text": "no_award_text_no_title_match",
}


def load(name, key):
    with open(OUT / name, newline="") as f:
        return {r[key]: r for r in csv.DictReader(f)}


def main():
    with open(ROOT / "data" / "contract_classifications.csv", newline="") as f:
        cls = {r["contract_number"]: r for r in csv.DictReader(f)
               if r["is_technology"] == "True"}
    pdf = load("tech_award_doc_solicitation_refs.csv", "contract")
    tm = load("tech_contract_title_matches.csv", "contract")

    rows = []
    for num in sorted(cls):
        c, p, t = cls[num], pdf[num], tm[num]
        score = float(t["score"])
        if p["status"] == "ref_found_in_pull":
            visibility = "visible_pdf_verified"
        elif score >= TITLE_MATCH_THRESHOLD:
            visibility = "visible_title_match"
        else:
            visibility = UNTRACED[p["status"]]
        rows.append({
            "contract": num,
            "title": c["title"],
            "agency": c["agency"],
            "primary_group": c["primary_group"],
            "ceiling": c["ceiling"],
            "visibility": visibility,
            "pdf_status": p["status"],
            "pdf_sol": p["doc_in_pull"],
            "title_match_score": t["score"],
            "title_match_sol": t["sol_num"],
            "title_match_sol_title": t["sol_title"],
        })

    with open(OUT / "tech_contract_solicitation_visibility.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    total = len(rows)
    visible = [r for r in rows if r["visibility"].startswith("visible")]
    ceil = sum(float(r["ceiling"] or 0) for r in rows)
    vceil = sum(float(r["ceiling"] or 0) for r in visible)
    print(f"technology contracts assessed: {total}")
    for v in sorted({r["visibility"] for r in rows}):
        print(f"  {v}: {sum(1 for r in rows if r['visibility'] == v)}")
    print(f"trace to a posted solicitation: {len(visible)} "
          f"({100 * len(visible) / total:.1f}%), "
          f"{100 * vceil / ceil:.1f}% of tech ceilings")


if __name__ == "__main__":
    main()
