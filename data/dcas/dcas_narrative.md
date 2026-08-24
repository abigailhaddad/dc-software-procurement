# DCAS: what it costs to keep one big system alive

DCAS is the DC Access System, the District's integrated eligibility system. It is
owned by the Department of Health Care Finance (DHCF), runs on the Curam case
management platform (IBM's, then Merative's after IBM spun off Watson Health),
and decides who in the District gets Medicaid, Alliance, SNAP, TANF, and a dozen
smaller benefits. Its public face is District Direct, the web portal and mobile
app. The system's own operations-and-maintenance solicitation describes it best:

> "DCAS is an integrated eligibility system providing District residents and
> case workers the ability to apply for or issue one or more of the below
> mentioned benefits through Intake processing (benefit applications),
> determining eligibility, and authorizing benefits online."
> — award document for contract CW112488 (D2Sol, DCAS O&M), Section C.1
> (saved excerpt: sources/award_pdf_CW112488_d2sol_dcas_om.txt)

and:

> "Over the past decade, the District has undergone several major
> implementations and today the District offers a caseworker portal, online
> portal, partner portal, and a mobile application. The DCAS ecosystem is
> publicly known as District Direct."
> — same document

Everything below is computed from the DC Contracts and Procurement Transparency
Portal snapshot in the dcbudget repo (contracts, payments, and cached award
PDFs; payments run 2016-10-06 through 2026-08-22) or quoted from a saved source
file. Ceilings and payments are kept apart throughout: a ceiling is permission
to spend, a payment is money that left.

## Where it came from (saved sources in `sources/`)

DCAS was the District's Affordable Care Act build. The Office of Contracting
and Procurement issued the RFP on July 12, 2012 — solicitation **CW16474,
"District of Columbia Access System (DCAS) Implementation"** — and its plan was
explicit:

> "DCAS will have three separate releases. Release I will be completed by
> October 1, 2013 with all required ACA functionality. Releases II and III
> (October 1, 2014 and September 1, 2015, respectively) will expand existing
> functionality for other Federal and local health and human services
> programs."
> — RFP Section C.1.4.5 (sources/hbx_dcas_rfp.pdf)

The RFP is built around CMS's enhanced 90/10 federal funding rules for
Medicaid eligibility systems (it cites the "Federal Funding for Medicaid
Eligibility Determination and Enrollment Activities" final rule and the CMS
"Seven Conditions and Standards"). Infosys Public Services won it: "The US
District of Columbia awarded Infosys Public Services ... a one-year contract
valued at $49.5 million to develop its new health benefit exchange, a crucial
component of the Affordable Care Act" (Infosys press release, June 14, 2013,
via Wayback Machine; sources/infosyspublicservices_2013_hbx_press_release.txt).
That solicitation number, CW16474, is the same contract number that appears on
$13.4M of Infosys payments in the portal's first visible year, FY2017 — the
tail of the original build, and the reason those payments are on the roster.

Release 1 powered DC Health Link's October 1, 2013 launch — minus its
centerpiece: "DC Health Link is not currently deploying the function that makes
new Medicaid eligibility determinations and calculates tax credits ... due to a
high error rate discovered through extensive systems testing" (HBX, Sept 25,
2013; sources/hbx_2013_dc_health_link_open_oct1.txt). A 2016 HHS OIG audit of
the DC marketplace (A-03-14-03301) records what running on a young DCAS was
like: the system "did not store a record when a customer attested to not being
incarcerated," and "Without HBX authorization or knowledge, on October 12,
2013, IPS turned off XML" logging (sources/hhsoig_A-03-14-03301_dc_marketplace.pdf).
Release 2 moved SNAP and TANF off the 1980s-era ACEDS system, piloted with
USDA Food and Nutrition Service permission in 2016, "Built on the IBM Curam
social program management solution" (PRNewswire, June 10, 2016;
sources/prnewswire_2016_infosys_modernize.txt).

Release 3 — non-MAGI Medicaid (long-term care waivers) plus the integrated
public portal — is where the repo's contract data picks up. DHCF announced in
April 2018 that OCP had "submitted a proposed contract to the Council of the
District of Columbia for KPMG, LLC (KPMG) to implement the final phase of the
District of Columbia Access System, known as Release 3 ('DCAS R3'). The
proposed multi-year contract includes three (3) base years and two (2) option
years. Prior to award, the contract must also be reviewed and approved by the
Centers for Medicare and Medicaid Services (CMS) and the Food and Nutrition
Service (FNS)" (sources/dhcf_dcas_release3_intent_to_award.txt). R3 went live
on November 15, 2021: "DHCF and DHS launched the final major release of the
DCAS system," and the portal and mobile app were rebranded — "Both offerings
are now called District Direct" (DHS Council oversight responses;
sources/dccouncil_dhs_fy21-22_prehearing_responses.txt).

## The capital ledger

The procurement portal is not where most DCAS money is appropriated. DCAS
lives in DC's Capital Improvements Plan as DHCF project **HT0-MES23C "DCAS
RELEASE 3"** (with siblings MES12C for the Release 1 era and HT0.DCASCA, the
"DCAS Capital Intra-District" project). The FY2019 CIP put Release 3's
"Estimated Full Funding Cost" at **$216,314,000**, first appropriated FY2017
(sources/dc_fy2019_budget_vol5_cip.pdf). DHCF's FY18-19 Council oversight
responses show the same project at $216,313,530 in lifetime budget authority,
$105.1M of it in the Capital Fund–Federal, and MES12C carrying $72.1M federal
(sources/dccouncil_dhcf_fy18-19_oversight_responses.pdf) — the money was
mostly Washington's, under the 90/10 match. By the Q2 FY2025 Capital Financial
Status Report, MES23C shows roughly **$225.5M in lifetime authority and
$214.8M spent through FY2024** across its two fund lines
(sources/cfsr_q2_fy2025_capital.pdf).

Operations moved into the operating budget: DHCF now has a whole **"DC Access
System (AMP069)" division** — $77.4M approved FY2025, $72.1M FY2026, $90.3M
proposed FY2027, the FY2027 jump driven by "an increase of $20,945,932 to
support nonpersonnel services costs in the DC Access System (DCAS) division"
(sources/dhcf_budget_chapter_fy2026.pdf, dhcf_budget_chapter_fy2027.pdf).

These budget-side numbers dwarf the procurement-portal view, and Council
members have done the full addition. Councilmember Christina Henderson,
announcing a December 2023 oversight roundtable: the District has spent "more
than $622 million on the DC Access System (DCAS) that was supposed to
streamline the enrollment process"
(sources/henderson_joint_roundtable_dcas.txt). The $284.4M this analysis can
tie to payments is the visible-procurement floor under that figure: it starts
in FY2017, misses the Infosys build years, and misses whatever is paid outside
the vendor-payment system.

## What the oversight record says

The system the money bought has a public performance record. The 2023
Henderson release again: "The District ranked last nationally for timely
processing of SNAP applications in Fiscal Year 2022 with a rate of just
42.86%," CMS "sent an August 2023 letter to DHCF citing concerns with the
District's application processing times," and — asked at FY2024 budget
oversight whether DCAS was working as intended — "Deputy Mayor Wayne Turnage
replied with an emphatic 'no'." The DC Inspector General's July 2025 Medicaid
eligibility audit (OIG No. 24-1-04JA) found, among six findings, that "DCAS
Did Not Consistently Reflect Correct or Accurate Eligibility Status": seven of
47 sampled beneficiaries "were incorrectly listed as active in DCAS despite
having been determined to be ineligible"
(sources/dcoig_report_24-1-04JA_medicaid_eligibility.pdf). Single Audit
findings route through the same system — one 2020 finding was resolved when
"The DHCF/DCAS triage team developed a system enhancement to accept an
'apostrophe' as a valid name character" in April 2021
(sources/dccouncil_dhs_fy21-22_prehearing_responses.txt). Meanwhile the
platform keeps moving under the contracts: Curam upgraded v7.0.11 to v8.1.1 in
July 2024, and the whole system migrated from on-premises hosting to AWS,
completed September 1, 2025 (DHCF FY25-26 oversight responses;
sources/dccouncil_dhcf_fy25-26_oversight_responses.pdf) — which is what the
Carahsoft AWS and Dynamic Systems "Cloud Migration" contracts in the roster
were buying.

The DC Auditor has published nothing on DCAS (site search saved:
sources/dcauditor_search_dcas.txt), and no GAO report names the system. For a
$600M-class program, the oversight shelf holds one DC OIG audit, federal
by-products (HHS OIG on the marketplace, Single Audit findings, FNS warning
letters), and Council hearings.

## Headline numbers

- **28 contracts** carry `system_name = DCAS` in the repo's classification:
  **$198,285,461** in lifetime ceilings.
- Widening to every contract whose portal title or classification names DCAS or
  Curam: **38 contracts, $230,230,440** in ceilings.
- Adding contracts the award PDFs and payment records tie to DCAS (the
  predecessor O&M contracts, Diona's mobile-app contracts, KPMG's
  legacy-numbered vouchers): **49 roster rows, $248,859,501** in visible
  ceilings — and even that is a floor, because several rows have no published
  ceiling at all.
- **Payments actually made that the data can tie to DCAS: $284,400,932** from
  FY2017 through 2026-08-22. Of that, $172,862,928 is tagged with a roster
  contract number on the voucher; $111,538,004 carries no contract number but
  belongs to vendors whose only visible DHCF business is DCAS (mostly KPMG and
  D2Sol — the inference for each row is recorded in the payments CSV).

Titles are floors. A contract does not have to say "DCAS" to be DCAS work: the
single biggest miss, CW95738 (NTE Systems, $17.7M ceiling), is titled just
"Operations and Maintenance (O&M)" — only its award PDF says "Staffing solution
to support the operations and maintenance of DCAS."

## Money over time

DCAS-associated payments by DC fiscal year (dollars, from
`dcas_payments_by_year.csv`; FY2017 begins 2016-10-01):

| FY | Paid | What was happening |
|---|---|---|
| 2017 | $17,124,281 | Infosys Public Services era (legacy contracts CW16474 at DHS, CW49026 at DHCF) |
| 2018 | $181,336 | Infosys tail; a visibility gap, not necessarily a spending gap |
| 2019 | $17,698,907 | KPMG's DCAS R3 build ramps up |
| 2020 | $34,954,939 | KPMG $28.0M; Accenture IV&V $6.4M |
| 2021 | $43,576,405 | KPMG $30.4M; IBM Curam $6.7M; Accenture $5.6M |
| 2022 | $54,834,403 | Peak: KPMG $34.9M; Accenture $7.7M; IBM $5.0M; CDE $3.7M |
| 2023 | $9,642,072 | R3 done, KPMG gone; the visible trough |
| 2024 | $21,467,225 | Merative Curam $12.4M; CDE, Diona, Maximus |
| 2025 | $39,574,996 | D2Sol O&M $27.9M; Merative $3.8M |
| 2026 | $45,346,369 | D2Sol $34.9M (through August); Merative $4.8M |

Two eras are visible. Through FY2022 the money went to building Release 3 —
KPMG was paid **$110,066,738** across FY2019–FY2022, peaking at $34.9M in a
single year, with Accenture's $23.7M IV&V watching the build. From FY2024 the
money goes to keeping the thing running: D2Sol invoices **$3,490,537.83 a
month** for operations and maintenance — $41.9M a year, every year, which is the
steady-state price of DCAS before licenses, mobile support, printing, or call
centers.

The FY2023 trough ($9.6M) and the near-empty FY2018 are the data's blind spots
as much as real lulls: the portal records no DCAS O&M payments between NTE
Systems' $1.9M (FY2022) and D2Sol's first vouchers (April 2025), yet the system
ran continuously — and D2Sol itself says "Since 2016, D2Sol has provided
operations and maintenance for MAGI, SNAP, and TANF implementations within the
District of Columbia Access System (DCAS)" (sources/d2sol_customers.txt).
Nearly a decade of one vendor's O&M work is visible in this data only from
April 2025 onward, presumably because the earlier years ran through
subcontracts and staffing vehicles the payment records don't label.

## The KPMG number, and why ceilings mislead

The portal's contract record for CW62227 "DCAS R3" shows a single option period
— Option 1, 7/31/2021–7/30/2022, **$7,281,205**, awarded 6/26/2018. Payments
against that same contract number total **$49,899,587** (FY2019–FY2022), seven
times the visible ceiling. Another **$60,167,151** of KPMG vouchers at DHCF in
those same years carry no contract number at all (a handful are stamped with
the legacy number "DHCF-2017-C-0026", two of them literally annotated
"DHCF-2017-C-0026 (CW62227)"). KPMG has no other DHCF contract in the portal.
DHCF's own 2018 announcement confirms the shape: a Council- and CMS/FNS-approved
multi-year engagement of "three (3) base years and two (2) option years," and
DHCF's FY18-19 oversight responses list CW62227 at $11,000,000 for a single
period. So the honest description is: the portal shows a $7.3M contract; the
checkbook shows a ~$110M systems-integration engagement, mostly federally
matched under the DCAS Release 3 capital project (see the capital ledger
above). "DCAS R3" was not a study — it
was the build, and Accenture's separately-contracted "DCAS R3 IV&V" ($23.3M
ceiling, $23.7M paid) was the District buying oversight of its builder.

The same pattern, smaller: Merative's Curam support contract CW111811 shows a
$4.2M ceiling and $8.0M in payments; Diona's CW99465 shows $101K and $421K paid;
GROW's learning-management tool shows $745K and $961K paid. The portal's
contract records hold whichever option periods survived into the current
system; payments hold the whole history since FY2017. When they disagree, the
payments are the better floor.

The mirror image also exists: **19 roster contracts totaling $37.3M in ceilings
show zero tagged payments** — including NTE's $17.7M O&M contract (its $1.9M of
actual payments carry no contract number) and $14.0M of "DCAS - Temporary
Personnel" awards. Some were barely used; some were paid under blank contract
fields; the data cannot tell which is which, only that ceilings are not spending
in either direction.

## The vendor cast

Roles are read from contract titles and award documents; dollars are ceilings
(C) or payments (P) as marked.

| Vendor | Role | Evidence |
|---|---|---|
| **KPMG** | Builder — Release 3 systems integrator | "DCAS R3" CW62227; $110.1M (P) FY2019–22 |
| **Accenture** | Overseer — IV&V on the R3 build | "DCAS R3 IV&V" CW67170; $23.3M (C), $23.7M (P) |
| **D2Sol** | Operator — runs DCAS today | "FY25 - DCAS - Operations Maintenance Services" CW121488 $83.7M (C)/2yrs + predecessor CW112488; $62.8M (P) FY2025–26 at $3.49M/month |
| **NTE Systems** | Operator (staffing), FY2022 — apparently brief | CW95738 $17.7M (C), $1.9M (P) |
| **Infosys Public Services** | Early operator/integrator (FY2017–18 tail of the original build) | $17.3M (P) on CW16474 — the 2012 “DCAS Implementation” RFP's own solicitation number — and CW49026; $49.5M original contract per 2013 press release |
| **IBM → Merative** | Licensor — Curam, the platform DCAS is built on | IBM "DCAS IBM Curam Software and Support" $11.7M (P) FY2021–22; Merative $21.0M (P) FY2024–26; MVS Inc. resold Curam licenses ($2.0M C) |
| **CDE Systems** | Builder — application development team | "DCAS Application Development Team" CW87331 $31.6M (C), $14.3M (P incl. untagged) |
| **Diona** | Builder — District Direct Mobile front end | "DCAS - Mobile Application Support" + 3 more; $10.5M (P incl. untagged) |
| **Carahsoft** | Reseller — AWS, Salesforce, cloud | 3 contracts, $5.7M (C) |
| **Maximus / Cerida** | Call center — Tier 1 customer service | "DCAS Tier 1 Customer Service Center"; $17.8M (C), $3.8M (P) |
| **Avid, Cradle, ZC, Sylver Rain, Analytica, NFF, Enlightened, Techflairs** | Staff augmentation — "DCAS - Temporary Personnel - [vendor]" | $27.8M (C) across 9 contracts, mostly zero tagged payments |
| **Dynamic Systems, Changing Tech, NJ3Q, BPM BI, Tharseo, GROW** | Small licensors/tooling — Oracle, JIRA, SonarQube, Informatica, Dynatrace, LMS | $2.5M (C) combined |
| **Immediate Mailing (+USPS)** | Notices — printing and mailing eligibility notices | $4.8M (C), $3.0M (P incl. $700K direct to USPS) |
| **LINK Strategic / bluebay** | Marketing; office supplies | $0.5M (C) |

**Rate disclosure:** across all 49 roster contracts, exactly one — Diona's
CW101930, a single program-manager slot at $201–220/hour — has labor rates in
the repo's extracted rate data. The $84M D2Sol O&M contract, every temporary-
personnel award, and the KPMG engagement disclose none. The District's steady-
state operator bills $41.9M a year with no public rate card behind it.

