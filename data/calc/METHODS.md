# DC labor rates vs. federally awarded GSA schedule rates (CALC+)

Date pulled: 2026-08-24. Federal index timestamp in every record: 2026-08-24T02:00:02Z
(the CEILINGRATES API refreshes nightly).

## Federal source
GSA's CALC tool was folded into buy.gsa.gov. Its successor is the **CALC+ Quick Rate
"Labor Ceiling Rates" tool** (https://buy.gsa.gov/pricing/qr/mas), powered by the public
**CEILINGRATES API** — no API key required:

    https://api.gsa.gov/acquisition/calc/v3/api/ceilingrates/

API docs: https://open.gsa.gov/api/dx-calc-api/

Every federal category was pulled with an EXACT title match, e.g.:

    https://api.gsa.gov/acquisition/calc/v3/api/ceilingrates/?search=labor_category:Web%20Developer&page=1&page_size=200

Verified empirically that `search=labor_category:X` returns only records whose
labor_category equals X (case-insensitive); e.g. "Web Developer" returns no
"Senior Web Developer" rows. All pages were fetched (page_size=200) until
fetched == hits.total.value; raw per-page responses are in raw/*_pN.json and
flattened per-category files in raw/*_all.json. raw/manifest.json records totals
and the query URL per category.

## Filters applied
- **Education**: none (all education levels included).
- **Experience**: none server-side. The comparison CSV applies a CLIENT-SIDE band on
  `min_years_experience` to approximate the DC Supply Schedule's four levels:
  Entry = 0–2, Journeyman = 3–5, Senior = 6–9, Master = 10–45 required years.
  This mapping is an analytic assumption (DC's published card does not define the
  levels in years). Banded percentiles are used only where band n >= 10; otherwise
  the row falls back to the all-experience distribution (the `experience_filter`
  column says which was used). All-experience stats are also carried on every row
  (federal_*_allexp columns).
- **Worksite**: none (records carry Contractor_Facility / Customer_Facility / Both;
  all included).
- **Business size / clearance / SIN**: none.

The statistic compared is `current_price` — the current-year not-to-exceed awarded
ceiling rate on an active MAS contract (all pulled records have contract_end >=
2026-09-29, so no expired rates; escalation is already reflected because
current_price is the currently effective year of each award's rate schedule).

## Like-for-like caveats
- DC rates (data/labor_rates.csv, pairing in {deterministic, proximate}) are
  not-to-exceed fully burdened ceiling rates from DC contract award documents.
- CALC+ rates are awarded MAS ceiling rates — also fully burdened NTE rates, but at
  the master-schedule level: agencies routinely negotiate discounts below them at
  task-order level, so CALC+ medians are, if anything, a HIGH benchmark for what the
  federal government actually pays.
- CALC+ ceiling rates are worksite-blended; DC work is largely on-site/DC-metro.
- Category titles are matched, not position descriptions; identical titles can carry
  different education/experience requirements across vendors.
- DC rates were awarded in various fiscal years (FY22+) and are compared against
  2026-current federal ceiling prices; a DC rate from FY22 faces ~2-4 years of
  federal escalation in this comparison. This biases the DC-vs-federal gap slightly
  more negative for older DC awards.
- "Customer Support" (DC) matched only "Customer Support Specialist" federally with
  n=5 — too thin to lean on; the Help Desk Specialist comparison (n=181) is the
  robust benchmark for that family.

## Matching policy (conservative: exact / near-exact titles only)
DC title -> federal CALC+ title:
  exact: Computer Scientist, Web Developer, Software Engineer, Systems Engineer,
         Cyber Security Engineer, Cyber Security Specialist, Enterprise Architect,
         Help Desk Specialist, Technical Writer, Project Manager, Program Manager,
         Business Analyst
  near-exact: Network Engineer/Specialist -> Network Engineer;
              Customer Support -> Customer Support Specialist (n=5, flagged)
DC categories in the target families that were NOT matched (numbered/suffixed
variants, e.g. "Project Manager III", "Sr. Technical Writer"):
see dc_categories_not_matched.txt (42 titles; their rows are excluded).

## Files
- pull_calc.py            — pull script (in analysis/)
- raw/                    — raw API responses (every federal number's source)
- build_calc_comparison.py — stats + CSV builder (in analysis/)
- dc_vs_calc_comparison.csv — one row per DC rate (119 rows)
- dc_categories_not_matched.txt
