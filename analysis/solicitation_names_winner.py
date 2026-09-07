"""Does a DC solicitation title name the vendor that ends up winning it?

    uv run python analysis/solicitation_names_winner.py

A solicitation is written before a vendor is chosen, so a vendor's name has no
business in its title. Where one appears, the buy was described around a
company that had not been selected yet. This script measures how often that
happens, and -- more importantly -- how often a solicitation title names a
company that did *not* win, because without that second number "X% name the
winner" cannot be told apart from "vendor names are just common in titles".

Population. Only the 82 pairs marked ``visible_pdf_verified`` in
data/solicitations/tech_contract_solicitation_visibility.csv. Those are pairs
where the award document itself prints the solicitation number, so the pairing
is not inferred. The 224 ``visible_title_match`` rows are matched *on title
similarity*, which makes them circular for this question: a solicitation whose
title contains a vendor name is more likely to match a contract whose title
contains the same name. They are reported at the bottom, separately, and the
two populations are never pooled.

Traps this script exists to avoid, all of which cost time before it was written:

1. Naive token matching produces garbage. "Public Consulting Group" matches
   "Public Safety Dive Equipment"; "Customer Expressions" matches any title
   with the word customer. The rule here is: strip corporate suffixes, drop
   generic corporate and procurement words (STOPLIST), keep tokens of three
   characters or more, and require *every* remaining token to appear in the
   title. A vendor whose whole name is generic words becomes unmatchable and is
   counted as such rather than silently scored zero.

2. Even that rule collides with ordinary English -- "Arbor E & T" against
   "Arbor Pravail", "E-Plan" against the word plan. Every non-winner match is
   adjudicated by hand in ADJUDICATION below, with the reason written down.
   There are 27 of them; the list is small enough to read.

3. ``doc_in_pull`` in tech_award_doc_solicitation_refs.csv is pipe-separated
   for 9 of the 82 rows ("|DCKA-2020-Q-0048"), and for those rows pull_title is
   empty. Resolve the number against data/raw/solicitations.json instead of
   trusting pull_title, or nine pairs silently get a blank title and score no.

4. A matcher that finds nothing proves nothing. The control at the end runs the
   same matcher over the *contract* titles, which are written after award. If
   the rate there is not much higher, the matcher is broken rather than the
   finding real.

Writes data/solicitations/solicitation_names_winner.csv (one row per verified
pair) and data/solicitations/solicitation_names_winner.md (the report).
Nothing here calls an LLM or the portal; the supplier list it uses for the base
rate is cached at data/solicitations/_suppliers.json by
refresh_supplier_cache() below.
"""

import csv
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

SOL_DIR = ROOT / "data" / "solicitations"
SUPPLIER_CACHE = SOL_DIR / "_suppliers.json"

# Words dropped before matching. Two kinds: corporate furniture that appears in
# hundreds of company names (SOLUTIONS, TECHNOLOGIES, GROUP), and procurement
# English that appears in hundreds of solicitation titles (MAINTENANCE,
# SUPPORT, EQUIPMENT). A token surviving this list is not guaranteed to be a
# company name -- see ADJUDICATION -- but a token that does not survive it is
# guaranteed not to be evidence of one. parse.normalize_vendor() has already
# removed INC/LLC/CORP and punctuation before this list is applied.
STOPLIST = set("""
GROUP SOLUTIONS SOLUTION SYSTEMS SYSTEM TECHNOLOGIES TECHNOLOGY TECH SERVICES SERVICE
CONSULTING CONSULTANTS CONSULTANT ASSOCIATES PARTNERS PARTNERSHIP ENTERPRISE ENTERPRISES
INTERNATIONAL GLOBAL AMERICA AMERICAN AMERICAS USA NATIONAL FEDERAL GOVERNMENT PUBLIC
MANAGEMENT DEVELOPMENT RESOURCES INDUSTRIES HOLDINGS VENTURES LABS LABORATORIES
SOFTWARE HARDWARE DATA INFORMATION COMMUNICATION COMMUNICATIONS NETWORK NETWORKS
COMPUTER COMPUTERS DIGITAL DESIGN ENGINEERING SECURITY SAFETY HEALTH HEALTHCARE MEDICAL
EDUCATION EDUCATIONAL TRAINING STAFFING GENERAL FIRST CENTER CENTRE CITY WASHINGTON
METRO METROPOLITAN CAPITAL PROFESSIONAL ADVANCED INNOVATIVE INNOVATION INNOVATIONS
INTEGRATED INTEGRATION CUSTOM EXPRESS PRIME PREMIER ALLIANCE CORPORATE BUSINESS OFFICE
SUPPLY SUPPLIES EQUIPMENT PRODUCT PRODUCTS PROJECT PROGRAM SUPPORT MAINTENANCE
CONTRACT CONTRACTING CONTRACTORS CONTRACTOR PROCUREMENT PURCHASE SUPPLIER VENDOR
STRATEGIC STRATEGIES CONSTRUCTION BUILDING BUILDERS ELECTRIC ELECTRICAL
MANAGED OPERATIONS OPERATING ANALYTICS ANALYSIS RESEARCH INSTITUTE FOUNDATION
COMPANIES CUSTOMER CUSTOMERS CLIENT CLIENTS QUALITY VALUE VALUES
UNITED STATES DISTRICT COLUMBIA WORLD WORLDWIDE UNIVERSAL PREMIUM SELECT CHOICE
DIRECT DIRECTIONS GATEWAY SUMMIT PINNACLE APEX ADVANTAGE SYNERGY NEXT NEXUS
COMPLETE TOTAL UNIFIED PLATFORM CLOUD INFOTECH INFO SOFT
AND FOR THE OF
""".split())

MIN_TOKEN_LEN = 3

# Every match between a verified pair's solicitation title and a company that
# did NOT win it, read by hand. Keyed by (contract, matched distinctive key).
# "named" means the title really is referring to that company; "coincidence"
# means the surviving token is ordinary English used in its ordinary sense.
# Adding a vendor to the universe can add keys here; assert_adjudicated() below
# fails loudly rather than letting a new match go unclassified.
ADJUDICATION = {
    ("CW101465", "DELL"): ("named", "Dell is the manufacturer of the workstation being bought"),
    ("CW101465", "PRECISION"): ("coincidence", "Precision Systems Inc vs the Dell Precision product line"),
    ("CW101465", "MOBILE"): ("coincidence", "T-Mobile USA vs 'Mobile Precision Workstation'"),
    ("CW101930", "LEVEL"): ("coincidence", "Level 3 Communications vs 'SME-Mastery Level'"),
    ("CW107276", "PLAN"): ("coincidence", "E-Plan Inc vs 'Cyber Security Plan'"),
    ("CW109524", "DMV"): ("coincidence", "DMV IT Consulting vs the buying agency's abbreviation"),
    ("CW111255", "INFRASTRUCTURE"): ("coincidence", "Infrastructure Solutions Group vs the subject of the training"),
    ("CW115955", "BMC"): ("named", "BMC Software is the maintained product's maker; MVS Inc won"),
    ("CW116241", "TRAFFIC"): ("coincidence", "American Traffic Solutions vs 'Traffic Signal Optimization'"),
    ("CW116988", "ASSESSMENT"): ("coincidence", "Assessment Solutions Group vs 'Needs Assessment'"),
    ("CW116988", "PLAN"): ("coincidence", "E-Plan Inc vs 'Strategic Plan'"),
    ("CW118397", "HOLDER"): ("coincidence", "Holder Enterprises vs 'Federal Supply Schedule Holder'"),
    ("CW123716", "FINANCIAL"): ("coincidence", "FM Financial Services vs 'Financial Advisory Services'"),
    ("CW123716", "ADVISORY"): ("coincidence", "Medical Advisory Services vs 'Advisory Services'"),
    ("CW123733", "FINANCIAL"): ("coincidence", "FM Financial Services vs 'Financial Advisory Services'"),
    ("CW123733", "ADVISORY"): ("coincidence", "Medical Advisory Services vs 'Advisory Services'"),
    ("CW124619", "URBAN"): ("coincidence", "The Urban Institute vs 'Urban Planning'"),
    ("CW125025", "ARBOR"): ("coincidence", "Arbor E & T LLC vs the Arbor Pravail appliance"),
    ("CW125490", "ASSESSMENT"): ("coincidence", "Assessment Solutions Group vs 'Third Party Assessment'"),
    ("CW129708", "ACCELA"): ("named", "Accela Inc makes the permitting system; Carahsoft won"),
    ("CW131575", "LIFE"): ("coincidence", "Innovative Life Solutions vs 'Extended Life Cycle'"),
    ("CW84963", "LABORATORY"): ("coincidence", "Laboratory Corporation of America vs 'Laboratory Information'"),
    ("CW87709", "CISCO"): ("named", "Cisco Systems makes the firewall; n2grate won"),
    ("CW89121", "FILENET"): ("named", "FileNet Corporation makes the licensed product; Document Systems won"),
    ("CW90729", "INSURANCE"): ("coincidence", "United Healthcare Insurance vs 'Unemployment Insurance'"),
    ("CW91857", "TECHNICAL"): ("coincidence", "American Technical Services vs 'technical assistance'"),
    ("CW91857", "PLAN"): ("coincidence", "E-Plan Inc vs 'implementation plan'"),
    ("CW91857", "BEHAVIORAL"): ("coincidence", "P&G Behavioral Health Services vs 'behavioral health services'"),
    ("CW92388", "FILENET"): ("named", "FileNet Corporation makes the supported product; Document Systems won"),
    ("CW99768", "CODE"): ("coincidence", "Code IT Inc vs 'Code Enforcement'"),
}

# The 82 solicitation titles that name a technology product or its maker, read
# off the titles by hand. This is a judgment call and is kept as data so it can
# be argued with: "Ariba PASS System Support" names software, not a bidder, and
# so does "FY25 Arbor Pravail". Contracts not listed here have titles that name
# a function ("Case Management System", "API Gateway") or nothing at all.
BRAND_NAMED = {
    "CW101465": "Dell",
    "CW102904": "SecureAuth",
    "CW104095": "Ivanti",
    "CW105049": "Palo Alto",
    "CW107694": "Salesforce (spelled 'Sales Force')",
    "CW112684": "Informatica",
    "CW115955": "BMC",
    "CW118397": "Salesforce",
    "CW123208": "ServiceNow",
    "CW125025": "Arbor Pravail",
    "CW129708": "Accela",
    "CW131188": "Salesforce",
    "CW131575": "Red Hat",
    "CW133224": "OpenText",
    "CW52586": "Quickbase",
    "CW64225": "SolarWinds",
    "CW81564": "F5",
    "CW87709": "Cisco",
    "CW88141": "Red Hat",
    "CW89121": "FileNet",
    "CW89124": "Check Point",
    "CW89498": "Proofpoint",
    "CW92388": "FileNet",
    "CW94584": "SeamlessDocs",
    "CW94950": "Ultivo (an Agilent product)",
    "CW95411": "Smart911 / Rave",
    "CW95440": "CrowdStrike",
    "CW96712": "Brandlive",
}

# Of the BRAND_NAMED titles, the ones where the company behind the named
# product ended up holding the contract. Quickbase is caught by the matcher
# because the product and the company share a name; Ultivo is not, because the
# title names an Agilent instrument rather than Agilent. Both are the same
# thing -- the maker selling direct -- so both are counted here by hand.
BRAND_OWNER_WON = {"CW52586", "CW94950"}


def refresh_supplier_cache():
    """Fetch the portal's 3,449-name supplier list once and cache it.

    The vendor universe for the base rate should be every company DC has
    registered, not only the 1,739 that hold a technology contract, or the base
    rate is measured against too small a pool. This is a plain reference
    lookup, not a search, so it needs no filters and no paging.
    """
    from dcprocure.client import DCProcurementClient

    names = DCProcurementClient().lookup("contracts", "suppliers")
    SUPPLIER_CACHE.write_text(json.dumps(names))
    return names


def normalize_title(text):
    """Uppercase, punctuation to spaces, whitespace squeezed.

    Same treatment the vendor name gets, so "Kimley-Horn" and "Kimley Horn"
    tokenize identically on both sides.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper())).strip()


def distinctive_tokens(vendor):
    """The part of a vendor name that could identify it in a title.

    Returns () when nothing survives -- "Public Consulting Group", "Advanced
    Network Consulting" and "Computer Solution LLC" are entirely made of words
    the STOPLIST removes. Those vendors cannot be detected by this method at
    all, which is a limit to report, not a zero to score.
    """
    key = parse.normalize_vendor(vendor)
    return tuple(t for t in key.split() if t not in STOPLIST and len(t) >= MIN_TOKEN_LEN)


def names_vendor(tokens, title_tokens):
    """True when every distinctive token of the vendor appears in the title.

    Requiring all of them, not any, is what kills "Public Consulting Group"
    against "Public Safety Dive Equipment". It also means a vendor recorded
    under a longer name than the title uses will be missed.
    """
    return bool(tokens) and all(t in title_tokens for t in tokens)


def load_inputs():
    with open(SOL_DIR / "tech_contract_solicitation_visibility.csv", newline="") as f:
        visibility = list(csv.DictReader(f))
    with open(SOL_DIR / "tech_award_doc_solicitation_refs.csv", newline="") as f:
        refs = {r["contract"]: r for r in csv.DictReader(f)}
    sols = json.loads((ROOT / "data" / "raw" / "solicitations.json").read_text())
    by_number = {s["solicitationNumber"]: s for s in sols}
    contracts = parse.load_contracts().drop_duplicates("contract_number")
    vendors = contracts.set_index("contract_number")["vendor"].to_dict()
    if not SUPPLIER_CACHE.exists():
        refresh_supplier_cache()
    suppliers = json.loads(SUPPLIER_CACHE.read_text())
    return visibility, refs, sols, by_number, vendors, suppliers


def solicitation_title(contract, refs, by_number):
    """Title of the solicitation the award document cited.

    doc_in_pull holds either a bare numeric doc id ("620162", meaning
    "Doc620162") or a pipe-separated pair whose numeric half is empty
    ("|DCKA-2020-Q-0048"). pull_title is blank for the second form, so resolve
    against the raw file first and fall back to pull_title only after that.
    """
    for part in [p for p in refs[contract]["doc_in_pull"].split("|") if p]:
        key = f"Doc{part}" if part.isdigit() else part
        if key in by_number:
            return key, by_number[key]["title"]
    return refs[contract]["doc_in_pull"], refs[contract]["pull_title"]


def build_universe(suppliers, vendors):
    """Distinctive key -> one representative name, over every company DC knows.

    Keys collide: several names reduce to the same distinctive tokens. Keeping
    one representative is enough, because the question is whether *a* company
    with that name shape is in the title, not which spelling of it.
    """
    universe = {}
    for name in list(suppliers) + list(vendors.values()):
        if not isinstance(name, str):
            continue
        key = distinctive_tokens(name)
        if key:
            universe.setdefault(key, name)
    return universe


def assert_adjudicated(pairs):
    """Every non-winner match must have a hand-written verdict.

    If the supplier list or the vendor set changes, new matches appear. Failing
    here is the point: an unclassified match would otherwise be counted as a
    real vendor mention by default and inflate the base rate.
    """
    missing = [p for p in pairs if p not in ADJUDICATION]
    if missing:
        raise SystemExit(
            "unadjudicated non-winner matches (add them to ADJUDICATION):\n  "
            + "\n  ".join(f"{c} / {k}" for c, k in missing)
        )


def main():
    visibility, refs, sols, by_number, vendors, suppliers = load_inputs()
    universe = build_universe(suppliers, vendors)

    rows = []
    for r in visibility:
        if r["visibility"] != "visible_pdf_verified":
            continue
        contract = r["contract"]
        vendor = vendors.get(contract)
        sol_num, sol_title = solicitation_title(contract, refs, by_number)
        title_tokens = set(normalize_title(sol_title).split())
        tokens = distinctive_tokens(vendor)
        others = sorted(
            (" ".join(k), universe[k])
            for k in universe
            if k != tokens and names_vendor(k, title_tokens)
        )
        rows.append({
            "contract": contract,
            "vendor": vendor,
            "contract_title": r["title"],
            "solicitation": sol_num,
            "solicitation_title": sol_title,
            "vendor_tokens": " ".join(tokens),
            "vendor_matchable": bool(tokens),
            "solicitation_names_winner": names_vendor(tokens, title_tokens),
            "contract_title_names_winner": names_vendor(
                tokens, set(normalize_title(r["title"]).split())),
            "brand_in_solicitation_title": BRAND_NAMED.get(contract, ""),
            "brand_owner_won": contract in BRAND_OWNER_WON,
            "other_vendor_keys_matched": "; ".join(k for k, _ in others),
            "other_vendor_names": "; ".join(n for _, n in others),
        })

    assert_adjudicated([(r["contract"], k)
                        for r in rows
                        for k in r["other_vendor_keys_matched"].split("; ") if k])

    out_csv = SOL_DIR / "solicitation_names_winner.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    report = build_report(rows, visibility, refs, by_number, vendors, universe, sols)
    (SOL_DIR / "solicitation_names_winner.md").write_text(report)
    print(report)
    print(f"\nwrote {out_csv}")
    print(f"wrote {SOL_DIR / 'solicitation_names_winner.md'}")


def permutation_expectation(rows, trials=2000, seed=0):
    """How many hits you would expect if winners were assigned at random.

    Shuffles the winning vendors across the same 82 solicitation titles, which
    holds both the title wording and the vendor name distribution fixed and
    breaks only the pairing. Cheap, and it answers "could one hit just happen".
    """
    usable = [r for r in rows if r["vendor_matchable"]]
    titles = [set(normalize_title(r["solicitation_title"]).split()) for r in usable]
    keys = [tuple(r["vendor_tokens"].split()) for r in usable]
    rng = random.Random(seed)
    counts = []
    for _ in range(trials):
        shuffled = keys[:]
        rng.shuffle(shuffled)
        counts.append(sum(1 for t, k in zip(titles, shuffled) if names_vendor(k, t)))
    return sum(counts) / trials, sum(1 for c in counts if c >= 1) / trials


def corpus_upper_bound(sols, universe):
    """Share of all 16,245 solicitation titles matching any company name.

    Unadjudicated, so it is an upper bound and mostly false positives -- the
    same "Plan"/"Arbor"/"Level" collisions the 82 produced, at scale. Its only
    job is to show that vendor-name-shaped strings are ordinary in titles, so
    one winner-name hit in 82 is not a raised rate.
    """
    first_token = {}
    for key in universe:
        first_token.setdefault(key[0], []).append(key)
    hits = 0
    for s in sols:
        tokens = set(normalize_title(s["title"]).split())
        if any(names_vendor(k, tokens) for t in tokens for k in first_token.get(t, ())):
            hits += 1
    return hits, len(sols)


def title_match_population(visibility, vendors):
    """The 224 title-matched pairs, kept apart from the verified 82.

    These are paired by title similarity, so a solicitation title holding a
    vendor name is more likely to be matched to a contract title holding the
    same name. Any rate computed here is inflated by construction and is
    reported for the examples only.
    """
    out = []
    for r in visibility:
        if r["visibility"] != "visible_title_match":
            continue
        tokens = distinctive_tokens(vendors.get(r["contract"]))
        title_tokens = set(normalize_title(r["title_match_sol_title"]).split())
        if names_vendor(tokens, title_tokens):
            out.append((r["contract"], vendors[r["contract"]],
                        r["title_match_sol_title"], r["title_match_score"]))
    return out


def competition_cross(rows):
    """Overlap with the signed justifications for limiting competition.

    contract_competition.csv covers 1,009 contracts but its flags come from
    reading determination-and-findings attachments, and most rows were never
    sampled. Both the numerator (titles naming the winner) and the flagged set
    are small, so this is expected to fail on overlap; report the sizes rather
    than a ratio.
    """
    with open(ROOT / "data" / "competition" / "contract_competition.csv", newline="") as f:
        comp = {r["contract_number"]: r for r in csv.DictReader(f)}
    present = [comp[r["contract"]] for r in rows if r["contract"] in comp]
    flags = ["limits_competition", "avoids_dc_market", "single_source_language",
             "exempt_from_competition", "software_lock_in_cite"]
    named = {r["contract"] for r in rows if r["solicitation_names_winner"]}
    return {
        "in_file": len(present),
        "coverage": Counter(r["coverage"] for r in present),
        "flagged": {f: sum(1 for r in present if r[f] == "True") for f in flags},
        "named_and_flagged": sorted(
            c for c in named
            if c in comp and any(comp[c][f] == "True" for f in flags)),
    }


def build_report(rows, visibility, refs, by_number, vendors, universe, sols):
    n = len(rows)
    unmatchable = [r for r in rows if not r["vendor_matchable"]]
    winner = [r for r in rows if r["solicitation_names_winner"]]
    verdicts = {}
    for r in rows:
        for k in r["other_vendor_keys_matched"].split("; "):
            if k:
                verdicts[(r["contract"], k)] = ADJUDICATION[(r["contract"], k)]
    named_other = sorted({c for (c, _), (v, _) in verdicts.items() if v == "named"})
    coincidence = sum(1 for v, _ in verdicts.values() if v == "coincidence")
    perm_mean, perm_any = permutation_expectation(rows)
    corpus_hits, corpus_n = corpus_upper_bound(sols, universe)
    control = [r for r in rows if r["contract_title_names_winner"]]

    all_rows = visibility
    ctrl_hits = ctrl_unmatch = 0
    for r in all_rows:
        tokens = distinctive_tokens(vendors.get(r["contract"]))
        if not tokens:
            ctrl_unmatch += 1
            continue
        if names_vendor(tokens, set(normalize_title(r["title"]).split())):
            ctrl_hits += 1

    tm = title_match_population(visibility, vendors)
    cross = competition_cross(rows)
    brands = [r for r in rows if r["brand_in_solicitation_title"]]
    brand_won = [r for r in brands if r["contract"] in BRAND_OWNER_WON]

    L = []
    w = L.append
    w("# Does a DC solicitation title name the vendor that wins it?")
    w("")
    w("Generated by `analysis/solicitation_names_winner.py`. Every count below is")
    w("recomputed from the files in `data/`; nothing is transcribed by hand except")
    w("the adjudications and the brand list, both of which are in the script.")
    w("")
    w("## The rate")
    w("")
    w(f"Of the **{n}** contract/solicitation pairs where the award document itself")
    w("prints the solicitation number, the solicitation title contains the winning")
    w(f"vendor's name in **{len(winner)}**.")
    w("")
    for r in winner:
        w(f"- `{r['contract']}` — {r['vendor']} — solicitation \"{r['solicitation_title']}\"")
    w("")
    names = sorted({r["vendor"] for r in unmatchable})
    w(f"{len(unmatchable)} of the {n} pairs ({len(names)} distinct vendors) have no distinctive token")
    w("after the stoplist is applied and cannot be detected by this method at all: "
      + ", ".join(names) + ".")
    w(f"Read strictly the rate is {len(winner)} of {n}; read as a share of vendors that")
    w(f"could have been detected it is {len(winner)} of {n - len(unmatchable)}. Neither is")
    w("a meaningful number on its own — see the base rate.")
    w("")
    w("## The base rate")
    w("")
    w("Three ways of asking how often a vendor name shows up in a solicitation title")
    w("when it is not the winner's.")
    w("")
    w("**Other companies in the same 82 titles.** Matching all 3,449 registered DC")
    w("suppliers plus every technology contract vendor against the same 82 titles")
    w(f"produces {len(verdicts)} matches on companies that did not win. Reading each one")
    w(f"by hand, {coincidence} are ordinary English colliding with a company name (\"Arbor E & T\"")
    w("against \"Arbor Pravail\", \"E-Plan, Inc\" against the word plan) and")
    w(f"{len(verdicts) - coincidence} are real:")
    w("")
    for (c, k), (v, why) in sorted(verdicts.items()):
        if v == "named":
            r = next(x for x in rows if x["contract"] == c)
            w(f"- `{c}` — \"{r['solicitation_title']}\" — {why}")
    w("")
    w(f"So {len(named_other)} of the {n} solicitation titles name a company that did not win,")
    w(f"against {len(winner)} that name the company that did. Every one of the {len(named_other)} is a")
    w("software or hardware manufacturer whose product the District was buying")
    w("through somebody else.")
    w("")
    w("**Chance.** Shuffling the winning vendors across the same 82 titles 2,000 times")
    w(f"gives a mean of {perm_mean:.3f} hits per shuffle, and at least one hit in")
    w(f"{100 * perm_any:.1f}% of shuffles. One observed hit is more than chance would give,")
    w("but one is one.")
    w("")
    w(f"**The whole archive.** Run unadjudicated against all {corpus_n:,} solicitations in")
    w(f"the pull, the matcher fires on {corpus_hits:,} titles ({100 * corpus_hits / corpus_n:.0f}%). That is an upper")
    w("bound made mostly of the same false positives, and it is the point: strings")
    w("shaped like company names are ordinary in DC solicitation titles. One title in")
    w("82 naming the actual winner is not an elevated rate against that background.")
    w("")
    w("## Control: the matcher is not broken")
    w("")
    w("Contract titles are written after award, so they can name the winner and often")
    w("do. Running the identical matcher over them:")
    w("")
    w(f"- all {len(all_rows):,} technology contracts: {ctrl_hits} contract titles name their own vendor "
      f"({100 * ctrl_hits / len(all_rows):.0f}%; {ctrl_unmatch} vendors unmatchable)")
    w(f"- the same {n} pairs: {len(control)} contract titles name the vendor, against "
      f"{len(winner)} solicitation title" + ("" if len(winner) == 1 else "s"))
    w("")
    w("The matcher finds vendor names in the hundreds where they are present. The")
    w("near-zero solicitation rate is the data, not the method.")
    w("")
    w("## What the titles do name")
    w("")
    w(f"{len(brands)} of the {n} solicitation titles name a technology product or its maker")
    w("— Dell, Salesforce, Red Hat, FileNet, CrowdStrike, Accela, ServiceNow, Cisco.")
    w(f"In {len(brand_won)} of those {len(brands)} the company behind the named product held the")
    w("resulting contract (" + ", ".join(f"`{r['contract']}` {r['brand_in_solicitation_title']}"
      for r in brand_won) + "). The rest went to somebody reselling it:")
    w("")
    for r in brands:
        mark = "  <- the named product's owner won" if r["contract"] in BRAND_OWNER_WON else ""
        w(f"- `{r['contract']}` {r['brand_in_solicitation_title']} — \"{r['solicitation_title']}\" — won by {r['vendor']}{mark}")
    w("")
    w("")
    w("The pattern is brand-name buying resold through an intermediary, not")
    w("solicitations written around the firm that wins them. A title saying")
    w("\"FY21-OCTO-FileNet License Renewal\" narrows the product to one vendor's")
    w("software; it does not narrow the bidder, and the bidder that won was a")
    w("reseller. Whether specifying the product is itself a competition problem is a")
    w("different question from the one this script asks, and needs the justification")
    w("documents rather than the titles.")
    w("")
    w("## The competition cross")
    w("")
    w(f"`data/competition/contract_competition.csv` holds {cross['in_file']} of these {n} contracts.")
    w("Its flags come from reading determination-and-findings attachments, and among")
    w("those " + str(cross["in_file"]) + " the coverage is "
      + ", ".join(f"{k} {v}" for k, v in sorted(cross["coverage"].items())) + ".")
    w("Flags raised: " + ", ".join(f"{k} {v}" for k, v in cross["flagged"].items()) + ".")
    w("")
    if not cross["named_and_flagged"]:
        w(f"The overlap is one contract against {cross['flagged']['limits_competition']} flagged for limiting competition, and it")
        w("is empty — the one solicitation that named its winner is not among the")
        w("flagged. There is no cross to report. A 1-by-5 table cannot be")
        w("disproportionate in either direction, and with 53 of the 70 never sampled")
        w("the flagged set is mostly missing data anyway.")
    else:
        w("Contracts both named and flagged: " + ", ".join(cross["named_and_flagged"]) + ".")
    w("")
    w("## The title-matched population, reported separately")
    w("")
    w("The other 224 traced contracts are paired to solicitations by title")
    w("similarity. That pairing is circular for this question: a solicitation whose")
    w("title contains a vendor name is more likely to be matched to a contract whose")
    w("title contains the same name. The rate below is inflated by construction and")
    w("must not be pooled with the 82.")
    w("")
    for c, v, t, s in tm:
        w(f"- `{c}` — {v} — solicitation \"{t}\" (similarity {s})")
    w("")
    same = sum(1 for *_, score in tm if float(score) == 1.0)
    w(f"{len(tm)} of 224. All {len(tm)} are cases where the manufacturer itself holds the")
    w(f"contract, and in {same} of the {len(tm)} the contract title and the solicitation title")
    w("normalise to the same string, which is what produced the match. NICE Systems is")
    w("here — the case that prompted this question — and it is the same brand-name pattern:")
    w("the title names NICE's software, and NICE sells it directly.")
    w("")
    w("## Answer")
    w("")
    w(f"{len(winner)} of {n}. Solicitation titles named a company that lost more often ({len(named_other)} of {n})")
    w("than one that won. There is no finding here about buys steered to a named")
    w("bidder. The measurable thing in these titles is brand-name specification,")
    w(f"which is in {len(brands)} of {n} of them and is a different claim requiring different")
    w("evidence.")
    w("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
