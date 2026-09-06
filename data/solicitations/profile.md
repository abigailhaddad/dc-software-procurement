# Solicitations data: profile, joins, and what it can defensibly say

> Written by hand on the 2026-08-24 snapshot. No script regenerates it, so
> `make drift` does not check it and the counts below will fall behind the
> portal. Re-derive before quoting any figure from it.

Source: `data/raw/solicitations.json`, 12,273 rows, pulled 2026-08-24 (manifest).
Universe: 35 currently-open solicitations plus the archive for FY2016–FY2026
(12,238 rows). The portal archive holds roughly 17,800 solicitations in total
(comment in `src/dcprocure/client.py`), so pre-FY2016 postings exist on the
portal but are not in this pull. Every count below is recomputed from the raw
file by the scripts in this directory.

## 1. Fields

Nine fields per row, nothing else:

| field | fill rate | notes |
| --- | --- | --- |
| solicitationNumber | 100% | **96.4% (11,835) are `Doc######` internal IDs, not procurement numbers** — see defects |
| title | 100% | 20 rows are "Untitled Sourcing Project" variants; 36 titles contain control chars |
| agencyNames | 100% | list; 141 distinct agencies; 51 rows contain a null entry in the list |
| marketType | 99.6% | free text, 49 spelling variants; normalizes to open / set-aside for 99.4% |
| closingDate | 100% | parses M/D/YYYY for 12,272 of 12,273 (one null) |
| closingTime | 100% | free text; "2:00 PM" on 55% of rows |
| specialist | 100% | name+email filled on 99.8% of rows; 357 distinct specialist emails |
| archive_fy | 99.7% | added by the pull (which archive year returned the row); null = open now |
| is_open | 100% | added by the pull |

**What is not in the data** (checked, not assumed): no posting/issue date — so
posting-to-close duration cannot be computed; no award outcome, awardee, or
link to a contract number; no bidder counts or bid results; no amendment
records; no dollar values; no description text or attachments; no commodity
codes; no cancellation status (5 titles mention "cancel"; that is all the
cancellation signal that exists).

## 2. Portal defects found (new)

1. **The solicitation list hides its own solicitation numbers.** 11,835 of
   12,273 rows (96.4%) carry a portal-internal document ID (`Doc806544`-style)
   in `solicitationNumber`; only 438 rows carry a real number
   (`DCKA-2026-B-0157`-style). The saving grace: award/contract forms print
   that same Doc ID in their "Solicitation Number" box, so the Doc ID works as
   a join key against award PDF text (verified below).
2. **`\x1a` (SUB) control characters** appear where dashes should be in 7
   solicitation numbers and inside 36 titles.
3. **`marketType` is a free-text box**, not an enum: 49 variants including
   trailing-space duplicates, commodity codes typed into it ("5576200",
   "890-78-00"), and category names ("Auditing Service").
4. Assorted rot: 20 untitled rows, one null closingDate, one duplicated real
   solicitation number (DCKA-2025-B-0070 ×2), 222 groups of exact
   title+closingDate duplicates (463 rows) that cannot be distinguished as
   amendments vs. repostings vs. double entry.

One field passes clean: `archive_fy` agrees with the fiscal year of
`closingDate` on all 12,238 archived rows (0 mismatches). Doc IDs rise roughly
with time but overlap across years — ordering signal only, not dates.

## 3. Volumes (see `solicitations_by_fy.csv`, `solicitations_by_agency.csv`)

- Posted solicitations **fell 45% over the decade**: FY2016 1,351 → FY2024
  1,007 → FY2025 749 (FY2026 has 725 with five weeks left). FY2025 is a
  complete year; unless the portal archives with multi-year lag, the decline
  is real in the portal's own terms.
- Market type: 52% set-aside, 47% open, ~1% other/unknown, roughly stable.
- 141 agencies post; OCTO leads (1,049), then DPR (752), OSSE (671), DDOT
  (657), MPD (607).
- **DCSS is loudly visible**: 2,034 solicitations (16.6%) name DCSS or the
  supply schedule in the title or market type — these are the RFQs run inside
  the DC Supply Schedule. 496 of them are technology-flagged. Explicit
  staff-augmentation/temporary-staffing titles: 115.
- Technology screen (keyword, on title only — the repo's LLM classifier does
  not run on solicitations): **1,839 flagged (15%)**. Audited on random
  samples: ~98% precision (40/40 flagged sample all plausibly tech, one
  borderline), recall misses ~2–7% (misses e.g. toner, patch cords, "license
  plate reader"). Treat as ±10%.
- Tech-flagged solicitations per year are flat-to-declining (172 in FY2016,
  125 in FY2025) while classified tech contract *awards* per year rise (13 →
  191). Do not read growth into the award side: the contracts module skews
  toward active/recent contracts (only 13 tech contracts dated FY2016
  survive), so the comparison is confounded by survivorship
  (`tech_solicitations_vs_awards_by_fy.csv` carries both columns anyway).

## 4. Joins (see `tech_contract_title_matches.csv`, `tech_award_doc_solicitation_refs.csv`)

There is **no shared key** between solicitations and contracts: contract
numbers are `CW######`, solicitations are Doc IDs or `DCXX-YYYY-L-NNNN`.
Two independent joins were built:

- **Award-PDF join (high precision).** Award forms print the solicitation
  number, which for portal-posted buys is the Doc ID. Scanning the 484 tech
  contracts with cached award text: 74 cite a solicitation that exists in the
  pull (Doc ID or real number — verified: the matched pairs' titles are
  near-identical), 31 cite a number *not* in the pull (pre-FY2016 like
  DCKT-2009-R-0120, or DDOT/DGS series the module doesn't carry), 62 cite only
  a schedule vehicle (DCSS-2007-D-920, GSA `GS-25F-…`, ITES/MOBIS/TS task-order
  tags), 7 print "N/A", and 310 (64%) show no reference at all — part genuine
  absence, part extraction failure (128 of the 310 have under 3,000 chars of
  text, i.e. scans or mod-only documents).
- **Title join (fuzzy).** Best-match on normalized titles with a
  closing-before-award constraint. Scored against the 67 PDF-verified pairs:
  the title join finds the same solicitation 53/67 times, and clears its 0.8
  threshold on 42/67 → **recall ≈ 63%**. Manual audit of 20 threshold matches:
  ~17–18 correct → **precision ≈ 85–90%**; the failure mode is recurring
  annual buys matched to an earlier year's identical solicitation.

Join rate, stated honestly: strict title matches cover 267/1,372 tech
contracts (19.5%); union with PDF-verified gives 295 (21.5%). A weak join is
the finding — see below.

## 5. The sharp question: which tech contracts have a visible solicitation?

Per-contract verdicts in `tech_contract_solicitation_visibility.csv`
(1,372 classified tech contracts; verdicts are prioritized — a contract that
both cites an out-of-pull number and clears the title threshold counts as
visible, which is why 31 citations in section 4 become 28 here):

| verdict | n | share |
| --- | --- | --- |
| visible — PDF-verified in the pull | 74 | 5.4% |
| visible — title match ≥0.8 | 221 | 16.1% |
| award cites a number absent from the pull | 28 | 2.0% |
| award cites only a schedule vehicle | 62 | 4.5% |
| award explicitly prints no solicitation | 6 | 0.4% |
| award text readable, no reference found | 249 | 18.1% |
| no readable award text and no title match | 732 | 53.4% |

**Observed: 295 of 1,372 tech contracts (21.5%) trace to a posted
solicitation. Ceiling-weighted it is starker: $528M of $4,641M — 11.4% of tech
ceiling dollars.** Correcting for the title join's measured ~63% recall lifts
the plausible count share to roughly 30%, no higher; even the generous bound
leaves two-thirds or more of tech contracts — and ~85–90% of tech ceiling
dollars — with no visible originating solicitation. The big-dollar side is
structural: the largest vehicles cite DCSS-2007-D-920 (a 2007 award) or GSA
schedules, which never pass through the solicitations module at all.

Caveats that bound this claim: the solicitation universe starts FY2016 (13
tech contracts are FY2016 awards whose solicitations could predate the
window, and 28 more cite out-of-window numbers); the 484 with award text
over-represent software_services (218 of 266) relative to the it_goods/other
groups; and 888 tech contracts publish no readable award document, so for
them "no visible solicitation" rests on the title join alone.

## 6. What the data cannot say

- Time from posting to close (no posting date).
- Cancellation, amendment, or award status of a solicitation.
- Number of bidders, or anything about competition beyond open vs set-aside.
- Solicitation dollar values.
- Whether the FY2025 posting decline reflects fewer buys or more buying
  through channels that never post (the visibility table suggests the latter
  exists at scale, but the solicitations file alone cannot separate them).

## Files

- `analyze.py`, `join.py`, `pdfscan2.py` — everything recomputable from raw
- `solicitations_by_fy.csv` — volumes, market type, tech/DCSS/staff-aug flags per FY
- `solicitations_by_agency.csv` — 141 agencies
- `tech_solicitations_vs_awards_by_fy.csv` — with the survivorship caveat above
- `tech_contract_title_matches.csv` — best title match per tech contract, scored
- `tech_award_doc_solicitation_refs.csv` — what each award text cites
- `tech_contract_solicitation_visibility.csv` — the per-contract verdict table
