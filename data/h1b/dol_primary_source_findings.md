# DOL primary source: SECONDARY_ENTITY names DC agencies directly

Written 2026-08-24. Source: DOL OFLC LCA disclosure files, FY2023 Q1 through
FY2026 Q3 (decisions 2022-10-01 through 2026-06-30), 13 workbooks downloaded
from dol.gov (Akamai blocks curl; fetched via browser), filtered to the vendor
watchlist + any Curam-titled filing, 1,725 rows kept:
data/h1b/dol/lca_dol_filtered.csv. Script: analysis/pull_h1b_dol.py
(uncommitted). Raw workbooks deleted after filtering per scope.

## (a) Filings that name a DC agency as the client (verbatim field values)

SECONDARY_ENTITY_BUSINESS_NAME, exactly as filed:

- D2Sol, Inc. -- "DC - Department of Health Care Finance (DHCF)" (and
  variants "Department of Health Care Finance (DHCF)", "DC Department of
  Health Care and Finance") on ~30 DC-worksite filings: Curam Developer /
  Senior Curam Developer / Curam Business Analyst / Curam QA Analyst / Curam
  ETL Developer / Curam Data Analyst / DevOps Engineer / "Software
  Developer/System Architect II". Worksite: 955 L'Enfant Plaza SW.
- ESYSTEMS INC. -- "DC ACCESS SYSTEM ADMINISTRATION (DCAS), DEPARTMENT OF
  HEALTH CARE FINANCE (DHCF)" on SENIOR CURAM DEVELOPER TEAM LEAD (multiple,
  2023-24, the $170-203K filings) and "DC ACCESS SYSTEM ADMINISTRATION (DCAS)
  DEPARTMENT OF HEALTH CARE FINANCE (DHCF)" on Information Technology Analyst
  and Senior Curam Developer. Worksite: 955 L'Enfant Plaza SW. This names
  DCAS itself -- the inference tier is closed.
- AVID SYSTEMS LLC -- "OFFICE OF THE CHIEF TECHNOLOGY OFFICER (OCTO)" and
  variants incl. "GOVERNMENT OF THE DISTRICT OF COLUMBIA, OFFICE OF THE CHIEF
  TECHNOLOGY OFFICER" (Systems Engineers, Information Security Analysts,
  Senior Network Security/Firewall Engineers, Unified Communication Engineer);
  "DHCF-DCAS" (SOA Administrator $150,250 and Sr. Middleware Engineer
  $140,000, 2025); "DISTRICT OF COLUMBIA, DEPARTMENT OF HEALTH CARE FINANCE"
  (Systems Engineer); "DC, DEPARTMENT OF HEALTH CARE FINANCE" (Senior Data
  Warehouse Specialist); "OFFICE OF THE ATTORNEY GENERAL (OAG), GOVERNMENT OF
  THE DISTRICT OF COLUMBIA" (Sr. Systems Engineer $160,000, 2025). Worksites
  include 200 I Street SE (OCTO) and 441 4th Street NW.
- CSZNET INC -- "Department of Employment Services" (Lead SQL DBA x2,
  worksite 4058 Minnesota Ave NE = DOES headquarters); "DC Government"
  (Network Engineer, 2720 Martin Luther King Jr Ave SE).
- Changing Technologies, Inc. -- "DC Health" (Data Surveillance Project
  Manager II, $55.00/Hour, 2201 Shannon Place SE, certified 2026-02-06);
  "DC Health Benefit Exchange Authority (DCHBX)" (System Analyst, $43.52/Hour,
  1225 I Street NW, certified 2026-03-10). THE MIRROR HAD ZERO FILINGS FOR
  THIS VENDOR -- all three are FY2026 decisions the mirror has not indexed.
- Diona (US) Inc. -- "District of Columbia Department of Healthcare Finance"
  (Principal Technical Consultant x3, 955 L'Enfant Plaza SW).
- DeNovo Systems -- "District of Columbia Department of Healthcare Finance
  (DHCF)" (Curam Developer x2).
- OOTB Minds, Inc. -- "DC - Department of Health Care Finance (DHCF)"
  (Curam Developer; Informatica ETL Developer).
- SRIVEN INFO INC (new, surfaced by the Curam-title filter) -- "Remote (Work
  from Home) for Department of Health Care Finance(DHCF)": a Curam Developer
  at $129,999/Year whose worksite is a residence in Cary, NC, certified
  2025-01-13. A remote DC-DHCF Curam worker invisible to any DC-worksite cut.

Reclassifications the secondary-entity field forces:
- INFOSYS PUBLIC SERVICES: its Washington DC worksite filings name "National
  Railroad Passenger Corporation(AMTRAK)" -- NOT DC government. The earlier
  MEDIUM tier for IPS DC filings should drop to WEAK for DC-gov claims.
- Serigor's one DC-worksite filing (QA Analyst) names "Beneficiary's home
  office" -- a residence, not a client site. Drop that data point.
- S3cloudsolutions: all 7 DC filings (1200 G St NW) have SECONDARY_ENTITY =
  "No" -- no client documented; the DC-gov tie rests on their job ads alone.

## (b) Certified-only vs the mirror's filed counts

DC-worksite rows FY2023+ (filed / certified): Avid Systems 17/13, D2Sol
34/33, eSystems 7/7, S3cloudsolutions 7/7, CSZNet 3/3, Changing Technologies
3/2 (one "Certified - Withdrawn"), Diona 3/2, Infosys Public Services 6/6
(Amtrak), OOTB Minds 2/2, DeNovo 2/2, Maximus 2/2, Serigor 1/1, Tecknomic 1/1.

Mirror drift: for most vendors the mirror is within a few filings of DOL once
the window difference is allowed for (mirror = submit year, DOL = decision
fiscal year). Two exceptions: eSystems -- mirror shows 85 filings submitted
2023+ vs 244 in DOL received 2023+ (of 285 FY2023+ decisions, 241 certified,
30 certified-withdrawn, 14 withdrawn): the mirror badly undercounts recent
eSystems activity, so mirror numbers are floors. And Changing Technologies --
mirror zero vs 3 DOL filings (FY2026 decisions not yet indexed by the mirror).

Caveats: certified LCA != visa issued != person hired; wages are attested
floors; the secondary-entity field is employer-written free text (spellings
vary) and is only present when the employer discloses a placement.
