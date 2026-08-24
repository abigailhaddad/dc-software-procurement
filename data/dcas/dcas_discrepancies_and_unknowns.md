# DCAS: what the data cannot establish

Every item below is a limit of the DC procurement portal snapshot in the
dcbudget repo (contracts, purchase orders, payments FY2017–2026-08-22, cached
award PDFs), stated so the roster and totals are read as floors, not censuses.

## Coverage limits

1. **Title-based rosters are floors.** The biggest single DCAS contract found
   outside the title net — CW95738, NTE Systems, $17.7M — is titled only
   "Operations and Maintenance (O&M)"; its award PDF names DCAS. There is no
   way to prove no other blandly-titled DCAS contract exists; only 637 award
   PDFs are cached, a fraction of all contracts.
2. **The payments table starts 2016-10-06.** DCAS was built roughly 2013–2016.
   The original design/development/implementation spend — the actual build
   cost — is entirely invisible here. The $284.4M payment total is FY2017
   onward only.
3. **Purchase orders carry no text.** The portal's PO records have vendor,
   agency, commodity code, and amount, but no description field, so POs cannot
   be searched for "DCAS." The $60.2M of KPMG vouchers without contract numbers
   reference PO numbers that do not appear in the repo's IT-commodity PO pull
   at all (likely coded to a non-IT commodity class).
4. **Six roster contract numbers are absent from the portal's contracts
   module** (confirmed by live ContractNumber queries, 2026-08-24): CW112488
   (D2Sol O&M), CW111054 and CW134380 (Merative), CW16474 and CW49026
   (Infosys), CW50549 (Enlightened, not rostered). Their existence is known
   only from payment rows and, for CW112488, a cached award PDF. Their
   ceilings, dates, and titles (except CW112488's) are unknown.
5. **The portal keeps only surviving option periods.** CW62227 (KPMG "DCAS R3",
   awarded 6/26/2018) shows one period worth $7.3M against $49.9M of tagged
   payments. Wherever payments exceed ceiling in the roster (7 contracts),
   assume missing periods, not overpayment proved.

## Attribution limits

6. **$111.5M of the payment total rests on vendor inference**, not a contract
   number on the voucher. Each row's reasoning is in `dcas_payments_by_year.csv`
   (`attribution` column). Strongest: D2Sol's null-contract vouchers are the
   identical $3,490,537.83 monthly invoice as its tagged DCAS O&M vouchers.
   Weakest: Infosys's CW16474 sits at the Department of Human Services, not
   DHCF; it is included because DCAS serves DHS programs and Infosys is the
   publicly reported early DCAS integrator, but the data alone does not prove
   those payments were for DCAS.
7. **Deliberately excluded, though possibly DCAS:** Accenture's $13.3M of
   null-contract DHCF payments (Accenture had other DHCF work); Maximus's
   $47.6M null-contract DHCF payments (Maximus runs several non-DCAS DHCF
   operations); Avid Systems' $37.0M null-contract DHCF payments (Avid has a
   dozen legacy DHCF staffing contracts). Some unknown fraction of these is
   DCAS money the total does not count.
8. **The FY2023–FY2024 operations gap.** Between NTE's last payment (FY2022)
   and D2Sol's first voucher (April 2025), no O&M-shaped payments appear, yet
   the system ran and D2Sol's predecessor contract CW112488 existed. Either
   O&M was paid under vouchers this analysis cannot attribute, or paid late;
   the data cannot say which, so FY2023's $9.6M understates true DCAS spending
   by an unknown amount.
9. **Duplicate payment rows exist in the portal data.** Two D2Sol vouchers
   appear twice each (same payment_id, once in a July 2026 check run, once in
   an August 2026 run) — $6,981,076 that naive summing double-counts. This
   analysis deduplicates on payment_id; the repo's headline figures elsewhere
   may not.

## Things the repo simply doesn't hold

10. **Total build cost of DCAS** (2013–2016 federal-plus-local capital spend).
    Requires CIP/budget documents and CMS grant records, not this portal.
11. **Labor rates.** 48 of 49 roster contracts publish no hourly rates in the
    repo's extracted rate data; the exception is one Diona program-manager slot
    ($201–220/hr). What D2Sol's $3.49M/month buys in people is not visible.
12. **Task-order internals.** Several awards ride DC Supply Schedule–style
    vehicles; pricing set at task-order level is not in the contract records.
13. **The federal/local funding split.** Eligibility systems are typically
    ~90% federally matched during development and 75% for M&O; nothing in the
    portal shows which of these dollars were reimbursed by CMS/FNS.

## From the public-record search (what the web could not establish either)

14. **The full multi-year value of KPMG's DCAS R3 contract.** DHCF's 2018
    press release gives no dollar figure; the FY18-19 oversight responses show
    CW62227 at $11,000,000 (apparently one period); the Council
    contract-approval resolution with the total was not found. The best
    available figure remains the ~$110M in payments computed here.
15. **An explicit DC statement of the federal match percentage for DCAS.**
    The 2012 RFP cites the CMS 90/10 enhanced-FFP rule and the capital tables
    show the federal fund shares concretely ($105.1M of MES23C, $72.1M of
    MES12C in Capital Fund–Federal), but no DC document was found saying
    "DCAS was X% federally funded."
16. **The exact local/federal split of the two MES23C lines in the Q2 FY2025
    CFSR** ($45.3M vs $180.2M lifetime authority) — the fund-table attribution
    comes from text-extraction context; verify against the PDF pages before
    quoting the split. Total across both: ~$225.5M authority, ~$214.8M spent
    through FY2024.
17. **Reconciling the $622M.** Councilmember Henderson's November 2023 figure
    ("more than $622 million") exceeds this analysis's $284.4M of visible
    payments plus the capital authority for the DCAS projects (MES23C's ~$225.5M, and at least MES12C's $72.1M federal share) in ways
    that cannot be decomposed from public documents — the operating-budget DCAS
    division, intra-District transfers (HT0.DCASCA), and pre-FY2017 spending
    all overlap unknowably with the procurement view. No public itemization of
    the $622M was found.
18. **No DC Auditor report on DCAS exists** (dcauditor.org search saved), and
    no GAO report names the system — so there is no independent audited
    account of total DCAS spending to check any of these numbers against.
19. **CMS certification status.** Eligibility-and-enrollment systems do not
    get MMIS-style certification; CMS funding approvals for R3 are documented,
    but no "DCAS certified" milestone exists to mark the build as finished in
    a federal sense.
