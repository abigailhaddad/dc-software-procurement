# Vendor-matched: H-1B LCA wages vs the same vendor's published DC rate card

Written 2026-08-24. Pairs file: vendor_matched_wage_vs_rate.csv (this directory).
LCA side: data/h1b/lca_filings.csv (h1bdata.info mirror of DOL LCA disclosures).
Card side: data/labor_rates.csv (rates extracted verbatim from DC award PDFs).
Hourly conversion: annual wage / 2080, no burden adjustment (divisor stated, nothing assumed).

## The defensible core: same company on both sides

Four vendors have both LCA filings and a published DC rate card: Avid Systems
(27 LCAs / 40-rate card, CW134571), Computer Aid (204 LCAs / 180-rate card,
CW91684 IT Staff Augmentation), Advanced Network Consulting (3 LCAs / 464-rate
card, CW73403+CW127666), Diona US (56 LCAs / 4-rate card, CW101930). No other
vendor in labor_rates.csv has any LCA filings (District Consultant, Changing
Technologies, V3Gate, Karder, Limbic Systems all card-only, zero LCAs).

Strongest pairs (see CSV for all ten):

- AVID: "IT PROJECT MANAGER" LCA $150,000/yr = $72.12/hr vs its own card
  category "Project Manager" at $150.00/hr NTE. Same words both sides. 2.08x.
- AVID: "INFORMATION SECURITY ANALYST" LCAs $96,000-$137,800 (Washington DC
  worksites, 2019-2025) = $46.15-$66.25/hr vs card "Cyber Security Specialist"
  $140.00/hr. 2.11x-3.03x.
- CAI: "SR. BUSINESS ANALYST" LCA $103,000 (Washington DC, 2019) = $49.52/hr
  vs its DC staff-aug card "Business Systems Analyst, Senior" $114.66/hr. 2.32x.
  (Vintage mismatch: LCA is 2019, card is from the later ITSA award.)
- AVID: "JR. DEVELOPER" / "JR. BUSINESS SYSTEMS ANALYST" LCAs $78,354/$78,374
  (Washington DC, 2024) = $37.67/$37.68/hr. Loose pair only: the card's lowest
  technical tier, "Technology Consultant I", is $110.00/hr. 2.92x if paired.

ANC and Diona pairs are LOOSE (their cards use management-consulting/PM
vocabulary with no engineering categories) - context only, do not headline.

## What this does and does not show

Across the ten pairs the card rate runs 1.8x to 3.5x the vendor's own attested
H-1B wage on an unburdened hourly basis. Two structural caveats mean the spread
is NOT margin:

1. An LCA wage is a floor the employer attests it will pay, not necessarily
   actual pay; actual pay can be higher.
2. A rate-card price is fully burdened and not-to-exceed; the LCA wage is bare
   salary. The spread includes employer taxes, benefits, overhead, bench time,
   and G&A - not just profit. Billed rates may also be below the NTE ceiling.

Also: nothing here shows that the specific LCA worker billed on the specific
contract behind the card. The pairing is company-to-company: the same firm's
own published DC price points against its own attested wages for its closest
matching titles, with pairing quality labeled per row (same-title, near-title,
loose).

## Context only (cross-vendor, do not headline)

DC-worksite Curam LCA wages (D2Sol, eSystems, Denovo et al.) have no same-vendor
rate card to pair against - none of the Curam filers publish a card in
labor_rates.csv. Any comparison of those wages to other vendors' cards or to
the CALC federal benchmarks is a cross-vendor join and belongs in context
paragraphs, labeled as such.
