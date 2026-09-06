"""How long the District takes to get from a closed solicitation to an award.

    uv run python analysis/solicitations_cycle_time.py

Runs after solicitations_pdfscan.py / solicitations_join.py /
solicitations_visibility.py, which decide *which* technology contracts trace
back to a posted solicitation. This script asks the next question: for the ones
that trace, how many days elapse.

Traps this script exists to record, because each one silently produces a wrong
number:

1. **There is no posting date.** The solicitations module publishes nine fields
   and a posting/issue date is not among them (see data/solicitations/profile.md
   section 1). The only solicitation date is ``closingDate``. So the metric here
   is close-to-award -- the evaluation and award lag -- not the full procurement
   cycle. The advertising period in front of it is unmeasurable from this data,
   and no amount of joining fixes that.

2. **The title join truncates its own cycle times, and inflates them.**
   solicitations_join.py discards any candidate whose close is more than 45 days
   after the award, so title-matched pairs cannot be more negative than -45 days
   by construction, while nothing bounds them above. Its documented failure mode
   -- a recurring annual buy matched to an identical solicitation from an
   earlier year -- lands entirely in the upper tail. Title-matched pairs are
   therefore reported separately and are not fit for a duration estimate. Only
   pairs where the award document itself prints the solicitation number carry a
   date interval that was not shaped by a date filter.

3. **Contracts repeat once per option period.** contract_details.json holds
   4,011 contracts in 7,166 rows -- three fewer than the 4,013 / 7,169 the
   search table carries and verify_claims.py checks, because a few option
   periods have no detail record. Award dates agree across a contract's rows in
   1,494 of the 1,648 multi-row cases but differ in 154, so the base period's
   row is picked explicitly rather than taking whichever row lands last.

4. **A long-lived schedule's closing date is not a bid close.** DCKA-2020-Q-0048,
   the DDOT FY20 Architect-Engineer schedule, sits open from 2020 until it
   archives in FY2026 with a closing date of 3/4/2026. Four task orders awarded
   off it in 2022 cite it, giving close-to-award intervals near -1,250 days. An
   award cannot precede the close of the bidding it came from, so pairs where
   the award leads the close by more than AWARD_BEFORE_CLOSE_TOLERANCE days are
   dropped as mis-resolved rather than reported as durations. Small negatives
   are kept: those are real, and awarding a few days before the advertised
   close is itself a fact about the data.

5. ``is_technology`` reads back from CSV as the string "True"; the classifier
   writes a numpy bool, so ``is True`` fails on the parsed object elsewhere.

The reference scan here is a superset of solicitations_pdfscan.py, which reads
only ``data/pdftext/``. Three more text caches were built later for other work
(``pdftext_ocr`` by ocr_awards.py, ``pdftext_df`` and ``pdftext_df_ocr`` by
competition.py) and hold award text for technology contracts the original scan
saw as having no document at all. Nothing here touches the network.
"""

import csv
import gzip
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "solicitations"

# Every cache of extracted award text in the repo. solicitations_pdfscan.py
# reads the first only; the rest were written by later work and are free signal.
TEXT_CACHES = ("pdftext", "pdftext_ocr", "pdftext_df", "pdftext_df_ocr")

DOCID = re.compile(r"\bDoc\s*(\d{5,7})\b")
DASH = r"[-–—]"
DCNUM = re.compile(
    r"\b(DC[A-Z]{2}|GAGA|CFOPD|DCPL|DCHA|POHC|CFSA)\s*" + DASH
    + r"\s*(\d{2,4})\s*" + DASH + r"\s*([A-Z]{1,2})\s*" + DASH + r"\s*(\d{3,5})\b"
)

# The three flags in contract_competition.csv that mean a signed document
# justified limiting or avoiding competition.
COMPETITION_FLAGS = ("limits_competition", "exempt_from_competition",
                     "single_source_language")

# Below this many overlapping contracts the competition cross is not reported
# as a number. Set by the brief; a difference of medians on single digits is
# not a finding.
MIN_CROSS_N = 25

# Days an award may precede its solicitation's closing date before the pair is
# treated as mis-resolved rather than fast. See trap 4 in the module docstring.
AWARD_BEFORE_CLOSE_TOLERANCE = 45


def parse_date(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y")
    except (TypeError, ValueError):
        return None


def normalize_sol_number(value):
    """Solicitation numbers carry \\x1a (SUB) where dashes belong on 7 rows."""
    return re.sub(r"[\s\x1a]", "-", (value or "").strip().upper())


def base_period_rows(details):
    """One row per contract number, preferring the base period.

    Falls back to the earliest award date when no row is labelled a base
    period, which happens when the portal drops the base row and leaves only
    option periods.
    """
    best = {}
    for row in details:
        number = row["contractNumber"]
        period = (row.get("currentOptionPeriod") or "").lower()
        award = parse_date(row.get("awardDate")) or datetime.max
        rank = (0 if period.startswith("base") else 1, award)
        if number not in best or rank < best[number][0]:
            best[number] = (rank, row)
    return {k: v[1] for k, v in best.items()}


def solicitation_indexes(sols):
    by_doc, by_number = {}, {}
    for sol in sols:
        raw = sol["solicitationNumber"].strip()
        match = re.fullmatch(r"Doc(\d+)", raw)
        if match:
            by_doc.setdefault(match.group(1), sol)
        by_number.setdefault(normalize_sol_number(raw), sol)
    return by_doc, by_number


def award_text_index():
    index = defaultdict(list)
    for cache in TEXT_CACHES:
        directory = ROOT / "data" / cache
        if not directory.exists():
            continue
        for path in directory.glob("*.txt.gz"):
            index[path.name[: -len(".txt.gz")]].append(path)
    return index


def cited_solicitations(details, texts, by_doc, by_number):
    """Contract number -> the solicitation its award document names.

    Doc IDs are checked first: they are the portal's own key and cannot collide
    with anything else in the text. A DCXX-YYYY-L-NNNN number is accepted only
    when that exact number is in the pull.
    """
    documents = defaultdict(set)
    for row in details:
        for doc in row.get("documents") or []:
            documents[row["contractNumber"]].add(doc["id"])

    cited = {}
    for number, ids in documents.items():
        blobs = []
        for doc_id in ids:
            digest = hashlib.sha1(doc_id.encode()).hexdigest()
            for path in texts.get(digest, []):
                blobs.append(gzip.open(path, "rt", errors="replace").read())
        if not blobs:
            continue
        full = "\n".join(blobs)
        for doc in sorted(set(DOCID.findall(full))):
            if doc in by_doc:
                cited[number] = by_doc[doc]
                break
        else:
            for num in sorted("-".join(m) for m in DCNUM.findall(full)):
                if num in by_number:
                    cited[number] = by_number[num]
                    break
    return cited


def market_type(value):
    """The portal's marketType is free text on both sides; 49 spelling variants
    exist on the solicitation side alone (profile.md defect 3)."""
    text = (value or "").lower()
    if "set" in text or "aside" in text:
        return "set_aside"
    if "open" in text:
        return "open"
    return "other"


def quantiles(values):
    ordered = sorted(values)

    def at(p):
        return ordered[min(len(ordered) - 1, int(p * len(ordered)))]

    return dict(n=len(ordered), min=ordered[0], p25=at(0.25),
                median=statistics.median(ordered), p75=at(0.75), p90=at(0.90),
                max=ordered[-1])


def describe(label, values):
    """Median and quartiles only. The tails here are mis-resolved matches, not
    slow procurements, and a mean over them goes negative."""
    q = quantiles(values)
    print(f"  {label}: n={q['n']:3d}  median={q['median']:5.0f}d  "
          f"p25={q['p25']:5d}d  p75={q['p75']:5d}d  p90={q['p90']:5d}d  "
          f"range {q['min']}..{q['max']}d")


def main():
    sols = json.load(open(ROOT / "data/raw/solicitations.json"))
    details = json.load(open(ROOT / "data/raw/contract_details.json"))
    with open(ROOT / "data/contract_classifications.csv", newline="") as f:
        tech = {r["contract_number"]: r for r in csv.DictReader(f)
                if r["is_technology"] == "True"}
    with open(OUT / "tech_contract_solicitation_visibility.csv", newline="") as f:
        visibility = {r["contract"]: r for r in csv.DictReader(f)}
    with open(OUT / "tech_contract_title_matches.csv", newline="") as f:
        titles = {r["contract"]: r for r in csv.DictReader(f)}

    by_doc, by_number = solicitation_indexes(sols)
    base = base_period_rows(details)
    cited = cited_solicitations(details, award_text_index(), by_doc, by_number)
    cited_tech = {k: v for k, v in cited.items() if k in tech}

    # -- what the extra caches bought -------------------------------------
    was_visible = {c for c, r in visibility.items()
                   if r["visibility"].startswith("visible")}
    ceilings = {c: float(r["ceiling"] or 0) for c, r in visibility.items()}
    total_ceiling = sum(ceilings.values())
    now_visible = was_visible | set(cited_tech)
    print(f"technology contracts: {len(tech)}")
    print(f"award document names a solicitation in the pull: {len(cited_tech)} "
          f"(solicitations_pdfscan.py, reading data/pdftext only, found 75)")
    print(f"trace to a posted solicitation: {len(was_visible)} -> "
          f"{len(now_visible)} "
          f"({100 * len(was_visible) / len(tech):.1f}% -> "
          f"{100 * len(now_visible) / len(tech):.1f}%), ceilings "
          f"{100 * sum(ceilings[c] for c in was_visible) / total_ceiling:.1f}% -> "
          f"{100 * sum(ceilings[c] for c in now_visible) / total_ceiling:.1f}%")

    # -- cycle times -------------------------------------------------------
    rows = []
    for number in sorted(now_visible):
        contract = base.get(number)
        if contract is None:
            continue
        if number in cited_tech:
            sol, source = cited_tech[number], "cited_in_award_doc"
        else:
            match = titles.get(number)
            key = normalize_sol_number(match["sol_num"]) if match else ""
            sol = by_number.get(key)
            source = "title_match"
        if sol is None:
            continue
        close = parse_date(sol["closingDate"])
        award = parse_date(contract.get("awardDate"))
        start = parse_date(contract.get("startDate"))
        if close is None or award is None:
            continue
        rows.append({
            "contract": number,
            "source": source,
            "agency": tech[number]["agency"],
            "primary_group": tech[number]["primary_group"],
            "ceiling": ceilings.get(number, 0),
            "solicitation": sol["solicitationNumber"].strip(),
            "solicitation_title": sol["title"].strip().replace("\n", " "),
            "solicitation_open": bool(sol.get("is_open")),
            "market_type": (sol.get("marketType") or "").strip(),
            "closing_date": sol["closingDate"],
            "award_date": contract.get("awardDate"),
            "start_date": contract.get("startDate"),
            "option_period": contract.get("currentOptionPeriod"),
            "days_close_to_award": (award - close).days,
            "days_close_to_start": (start - close).days if start else "",
        })

    with open(OUT / "tech_contract_cycle_time.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    still_open = [r for r in rows if r["solicitation_open"]]
    mis_resolved = [r for r in rows
                    if r["days_close_to_award"] < -AWARD_BEFORE_CLOSE_TOLERANCE]
    usable = [r for r in rows if not r["solicitation_open"]
              and r["days_close_to_award"] >= -AWARD_BEFORE_CLOSE_TOLERANCE]
    print(f"\npairs carrying both dates: {len(rows)}")
    print(f"  dropped, solicitation still open: {len(still_open)}")
    print(f"  dropped, award precedes the close by more than "
          f"{AWARD_BEFORE_CLOSE_TOLERANCE} days: {len(mis_resolved)} "
          f"({Counter(r['solicitation'] for r in mis_resolved).most_common(1)})")
    print(f"  usable: {len(usable)}")

    cited_rows = [r for r in usable if r["source"] == "cited_in_award_doc"]
    title_rows = [r for r in usable if r["source"] == "title_match"]

    print("\nclose-to-award days, by how the pair was traced:")
    describe("cited in the award document", [r["days_close_to_award"] for r in cited_rows])
    describe("title match only          ", [r["days_close_to_award"] for r in title_rows])

    over_year = sum(1 for r in title_rows if r["days_close_to_award"] > 365)
    over_two = sum(1 for r in title_rows if r["days_close_to_award"] > 730)
    print(f"\n  {over_year} of {len(title_rows)} title-matched pairs exceed 365 "
          f"days and {over_two} exceed 730. The title join's documented failure "
          f"mode -- a recurring buy matched to an identical solicitation from an "
          f"earlier year -- lands entirely in that tail, so the title-matched "
          f"pairs are not read as durations below.")
    early = [r["days_close_to_award"] for r in cited_rows
             if r["days_close_to_award"] < 0]
    print(f"  {len(early)} cited pairs were awarded before the advertised "
          f"closing date, by up to {abs(min(early)) if early else 0} days.")

    print("\ncited pairs, close-to-award, by what was bought:")
    for group in sorted({r["primary_group"] for r in cited_rows}):
        values = [r["days_close_to_award"] for r in cited_rows
                  if r["primary_group"] == group]
        if len(values) >= 10:
            describe(f"{group:26s}", values)

    # -- market type, the one competition-shaped split with usable counts ---
    contract_market = {}
    for row in details:
        number = row["contractNumber"]
        period = (row.get("currentOptionPeriod") or "").lower()
        if number not in contract_market or period.startswith("base"):
            contract_market[number] = row.get("marketType")

    agreement = Counter((market_type(contract_market.get(r["contract"])),
                         market_type(r["market_type"])) for r in cited_rows)
    same = sum(v for k, v in agreement.items() if k[0] == k[1])
    print(f"\ncontract-side and solicitation-side marketType agree on {same} of "
          f"{len(cited_rows)} cited pairs; the contract-side field is used below.")

    print("\ncited pairs, close-to-award, by market type:")
    for kind in ("set_aside", "open"):
        values = [r["days_close_to_award"] for r in cited_rows
                  if market_type(contract_market.get(r["contract"])) == kind]
        if values:
            describe(f"{kind:26s}", values)
    print("  ceiling is a plausible confounder -- set-asides are the smaller "
          "buys -- so the same split within each size band:")
    for label, test in (("under $1M", lambda c: c < 1e6),
                        ("$1M and over", lambda c: c >= 1e6)):
        for kind in ("set_aside", "open"):
            values = [r["days_close_to_award"] for r in cited_rows
                      if test(r["ceiling"])
                      and market_type(contract_market.get(r["contract"])) == kind]
            if values:
                describe(f"{label} / {kind}".ljust(26), values)
    check = [r for r in title_rows if r["days_close_to_award"] <= 365]
    print(f"  the contaminated title-matched pairs, trimmed to 0-365 days, as a "
          f"direction check only:")
    for kind in ("set_aside", "open"):
        values = [r["days_close_to_award"] for r in check
                  if market_type(contract_market.get(r["contract"])) == kind]
        if values:
            describe(f"{kind:26s}", values)

    # -- can the traceable share be raised further? ------------------------
    # The cited pairs are ground truth: the award document printed the number.
    # Scoring the title join against them says where its threshold could move.
    # solicitations_visibility.py uses 0.80. The solicitation record carries a
    # contracting specialist, and contract_details carries a specialist and an
    # officer; requiring the two to name the same person is a corroborating
    # signal the existing join never uses.
    specialist_email = {}
    for sol in sols:
        key = normalize_sol_number(sol["solicitationNumber"])
        specialist_email.setdefault(
            key, ((sol.get("specialist") or {}).get("email") or "").lower())

    def corroborated(number, sol_key):
        contract = base.get(number) or {}
        sol_email = specialist_email.get(sol_key, "")
        staff = {(contract.get("contractingSpecialistEmail") or "").lower(),
                 (contract.get("contractingOfficerEmail") or "").lower()}
        return bool(sol_email) and sol_email in staff

    print("\ntitle-join accuracy by score band, scored against the "
          f"{len(cited_tech)} pairs the award documents name outright:")
    bands = ((0.9, 1.01), (0.8, 0.9), (0.7, 0.8), (0.6, 0.7), (0.5, 0.6), (0.0, 0.5))
    for low, high in bands:
        picked = correct = corr_n = corr_correct = 0
        for number, sol in cited_tech.items():
            match = titles.get(number)
            if match is None or not low <= float(match["score"]) < high:
                continue
            key = normalize_sol_number(match["sol_num"])
            hit = key == normalize_sol_number(sol["solicitationNumber"])
            picked += 1
            correct += hit
            if corroborated(number, key):
                corr_n += 1
                corr_correct += hit
        if picked:
            print(f"  score {low:.1f}-{high:.1f}: {correct:2d}/{picked:2d} correct"
                  f"   with a shared specialist: {corr_correct:2d}/{corr_n:2d}")

    would_add = set()
    for number, row in visibility.items():
        if number in now_visible:
            continue
        match = titles.get(number)
        if match is None or not 0.6 <= float(match["score"]) < 0.8:
            continue
        if corroborated(number, normalize_sol_number(match["sol_num"])):
            would_add.add(number)
    combined = now_visible | would_add
    print(f"  dropping the threshold to 0.60 where the specialist corroborates "
          f"would add {len(would_add)} contracts: {len(combined)} traced "
          f"({100 * len(combined) / len(tech):.1f}%), ceilings "
          f"{100 * sum(ceilings[c] for c in combined) / total_ceiling:.1f}%. "
          f"Not adopted here -- the bands that justify it hold single-digit "
          f"counts, and the gain does not change what the data supports.")

    # -- the competition cross --------------------------------------------
    with open(ROOT / "data/competition/contract_competition.csv", newline="") as f:
        competition = {r["contract_number"]: r for r in csv.DictReader(f)}
    flagged = {n for n, r in competition.items()
               if any(r[f] == "True" for f in COMPETITION_FLAGS)}
    assessed = {n for n, r in competition.items() if r["coverage"] != "not_sampled"}
    timed = {r["contract"] for r in usable}
    timed_cited = {r["contract"] for r in cited_rows}

    print("\ncompetition cross:")
    print(f"  technology contracts carrying a signed justification for limiting "
          f"or avoiding competition: {len(flagged)}")
    print(f"  of those, close-to-award is computable: {len(flagged & timed)} "
          f"({len(flagged & timed_cited)} in the high-precision cited set)")
    print(f"  contracts with a computable cycle time that were sampled for "
          f"competition at all: {len(assessed & timed)}; the other "
          f"{len(timed - assessed)} were never opened, so carrying no flag is "
          f"not evidence that they were competed")
    if len(flagged & timed) < MIN_CROSS_N:
        print(f"  -> under {MIN_CROSS_N}. No comparison is reported. A median "
              f"built on this many contracts cannot separate a real difference "
              f"from the spread already visible in the tails above.")
    else:
        describe("competition limited       ",
                 [r["days_close_to_award"] for r in usable
                  if r["contract"] in flagged])
        describe("sampled, no such document ",
                 [r["days_close_to_award"] for r in usable
                  if r["contract"] in assessed and r["contract"] not in flagged])

    print(f"\nwrote {OUT / 'tech_contract_cycle_time.csv'}")


if __name__ == "__main__":
    main()
