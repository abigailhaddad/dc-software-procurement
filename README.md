# How the District buys software

> **Read the caveats with the numbers.** Every figure here is recomputed from the
> published data by `analysis/verify_claims.py` and checked. Some rest on a model
> reading contract text rather than on the District's own records, and those carry
> a measured error rate: `docs/CLASSIFIER_ACCURACY.md` says which, and how far off
> each is. `docs/NEXT_STEPS.md` is what remains.

Pulls the DC Contracts and Procurement Transparency Portal, sorts every contract
by what it actually buys, and reads how the work is structured out of the award
PDFs.

The briefing is `site/index.html`: open it in a browser, and the contracts
behind each figure are a click away. Every number on it is recomputed by
`analysis/verify_claims.py`, which fails if one has drifted.

## Run it

```bash
make install
make pull        # ~10 min, writes ~360MB into data/raw
make classify    # needs OPENAI_API_KEY in .env
make pull-payments  # refresh just the payments table
make verify      # recomputes every published figure, fails on drift
make test        # scores the classifiers against hand-read labels
```

`make help` lists the rest. Every step is resumable: pulls skip what is already
on disk and classification is cached by content, so rerunning costs nothing.

## What it found

Of 1,383 technology contracts carrying $4.7B in ceilings, custom software
development accounts for 62 contracts and $39M. That is a floor on custom
development in contract form; building also happens inside the $1.2B of staff
augmentation and inside task orders this data cannot see. Ordering vehicles
carry $1.9B. Half of the $579M in traceable technology payments went to ten
vendors, and 17% to one — measured on the 44% of contracts whose payments carry
their number, though the top vendor is corroborated separately by the
purchase-order table. 60 award documents publish hourly rate cards — 2,581
not-to-exceed rates, $23.68 to $1,015 — and job ads recruiting for the same
tier names show what the workers are offered against them.

How the work is organised is usually not written down. Of 80 award documents
read by hand, 25 describe a delivery process; the rest have to be read off the
price schedule, off what the work plainly is, or off nothing at all. Two readers
given the same 30 documents agree 83% of the time, and the classifier agrees
with the hand labels 57% of the time, so no six-way breakdown is published.

What contracts do after they are awarded is better evidenced. 1,609 contracts
carry 8,547 modifications, and counting those mostly measures how long a
contract has run: one option period averages 1.3, three averages 5.3. Holding
that roughly fixed, the 15 build contracts whose award document fixes scope in
advance average 6.5 modifications against 4.7 for the other 106. Fixing scope
in the award document does not go with fewer changes afterwards, though 15
contracts is thin and the label separating the two groups is the 57% one. Of
400 modification documents read, 279 were legible, and 27 of those change what
is being built or when it is due.

## Refreshing the payments table

`make pull` fetches everything, including the full payments table -- about
870,000 voucher lines in a single 300MB response. The payments module ignores
every filter it is given, so there is no way to pull a slice of it: the pull
instead checks that what came back is the whole table, and refuses to overwrite
the snapshot if it is not. To refresh just payments without re-pulling contracts
and solicitations, run `make pull-payments`.

## Drift: what the weekly refresh reports

`make verify` recomputes every published figure from the snapshot and fails if
one no longer holds. The asserted figures in `analysis/verify_claims.py` are the
published baseline -- what the briefing, the site and this README currently say
-- and changing one is a decision made after looking at why it moved.

The portal is live, so those figures drift. `make drift` runs the same checks and
reports the movement instead of failing on it: how many claims were checked, how
many moved, and for each one the published value, the current value and the
change. Reports land in `data/drift/`, named for the snapshot date in
`data/raw/manifest.json`, with `latest.txt` and `latest.json` pointing at the most
recent run. It exits zero whether or not anything moved, and non-zero only when
the run itself breaks.

`scripts/weekly_refresh.sh` chains those together -- pull, classify, rebuild the
derived tables, report drift, rebuild the dashboard -- and
`scripts/install_schedule.sh` installs it as a weekly launchd job.
`site/dashboard.html` is in the chain because it reports the current snapshot;
`site/index.html` is not, because the briefing stays on the published baseline.
Nothing in that chain rewrites the briefing: republishing is a human decision
made after reading the drift report. `docs/SCHEDULING.md` has the details.

## The portal has a word for sole source, and never uses it for technology

`marketType` can say Sole Source: 24 contracts across the corpus do, across
five spellings of it. No technology contract ever does. Across all 1,383 the field takes only Set Aside,
Open, GSA, DCSS and Agreement, so nothing in the published data records that an
award was not competed.

The justifications exist as documents. A Determination and Findings is what a
contracting officer signs to limit competition, exempt a purchase from it, or
buy from a single available source, and 21,722 of them are attached to
contracts. Of the technology contracts whose D&Fs have been read, 75 carrying
$63.3M of ceiling have one. Forty-four of those 75 are labelled Open.

Nine cite PPRA 2-354.13(8), maintenance of existing software where the vendor
owns the code: "The EGMS application is built on the proprietary software
created and owned by MTW Solutions, LLC; therefore, no other Contractor has
access to the rights and source codes."

A third of that rests on machine transcription. 1,009 technology contracts carry
a D&F; 294 have had one read from its own text layer and 63 more were scans,
recovered by running OCR over them. 25 of the 75 findings disappear without
those transcriptions, so the rows carrying them are marked in
`data/competition/contract_competition.csv` and can be counted separately.

Reading the scans mattered because they were not randomly distributed. Among the
documents named for sole source, 56% had no text layer, against 7% of the
cooperative purchases -- the unreadable ones were disproportionately the ones
that would carry a finding, so leaving them out understated the answer in the
direction that flatters the portal.

75 is still a floor. 652 of the 1,009 have not been sampled at all.

## Why the commodity codes are not used

They are assigned by the buyer and applied loosely. Class 918, "consulting
services", holds Medicaid managed care contracts worth billions. Class 920,
"data processing, computer, programming and software services", holds a plumbing
contractor's $14M contract. The District's largest IT staffing vehicles —
including one titled "Information Technology Staff Augmentation Services" —
are filed under 961/962, "miscellaneous services, not otherwise classified",
while the purchase orders written against them are coded 918.

So contracts are sorted by reading them. `classifier_config.yaml` holds the
schema and prompt for what a contract buys; `delivery_config.yaml` does the same
for how the work is structured. Accuracy is scored against contracts labelled by
hand in `tests/`, blind. On a random sample the work-type classifier agrees with
the hand labels 90% of the time and `is_technology` 95%; `delivery_structure`
agrees with its 80 hand labels 57% of the time, against a ceiling of 83% set by
what two human readers agree on, which is why no delivery-structure breakdown
is published. `docs/CLASSIFIER_ACCURACY.md` has the measurements and what they
were run on.
On what agile language is doing the model scores 18 of 20; the two misses are
genuine commitments it filed as incidental, corrected by hand in
`delivery_overrides.yaml`, with reasons, and the eval expects exactly those two.

The work-type miss is case 21, and it is not corrected anywhere. "DOH FY25 Nurse
Staffing Temporary Support Services" is nurse staffing, and the model files it as
technology staff augmentation, because "temporary support services" reads as staff
augmentation whatever the staff do. It is in the eval because it was caught in the
wild: it was labelled not_technology in August and moved in September, so it is
counted in the technology figures now. Nothing overrides work_type, so that
inflation stands in the published numbers until the taxonomy or the prompt
distinguishes staffing a hospital from staffing a help desk.

## Layout

| Path | What it does |
| --- | --- |
| `src/dcprocure/client.py` | The portal API, with guards against its silent-filter behaviour |
| `src/dcprocure/pull.py` | Fetch raw JSON into `data/raw/` with a provenance manifest |
| `src/dcprocure/parse.py` | Normalise money, dates, fiscal years, vendor names |
| `src/dcprocure/documents.py` | Fetch award PDFs, cache their text, pull out the relevant sections |
| `src/dcprocure/classify.py` | LLM classification, driven by the YAML configs |
| `src/dcprocure/analysis.py` | Summary tables |
| `classifier_config.yaml` | What a contract buys: schema, prompt, domain rules |
| `delivery_config.yaml` | How the work is structured, and what agile language is doing |
| `vendor_aliases.yaml` | Which vendor spellings are one company, and which are deliberately not |
| `delivery_overrides.yaml` | Hand-read corrections applied on top of the delivery classifier, with reasons |
| `site/index.html` | The briefing as a single static page, frozen on the published baseline |
| `site/dashboard.html` | The current snapshot and how far it has moved from that baseline |
| `analysis/rebuild_manifest.py` | Rebuild the provenance manifest from the files on disk |
| `analysis/ocr_awards.py` | OCR scanned award documents with a vision model; results cached and classified separately |
| `analysis/pull_h1b.py` | Pull public H-1B wage filings for the vendor set (`make h1b`); DOL's disclosure files are authoritative |
| `analysis/pull_h1b_dol.py` | Filter DOL's own LCA disclosure files to the vendor set — case status, worksite address, and the client-naming secondary-entity field |
| `analysis/extract_rates.py` | Pull hourly rate cards out of the award texts, every rate verified verbatim |
| `data/labor_rates.csv` | The 2,581 published rates, with whether each title-to-price pairing verified |
| `analysis/modifications.py` | Count and read contract modifications (`make modifications`); the deduplication trap is in its docstring |
| `analysis/build_site_data.py` | Rebuild the drill-down data behind the site (`make site`) |
| `evidence/job_ads/` | Job postings quoted in the briefing: screenshots, raw pages, archive links |
| `analysis/pull_calc.py` | Pull GSA CALC awarded ceiling rates, the federal comparison set (`make calc`) |
| `analysis/build_calc_comparison.py` | Match DC's published rates to federal titles and compare them |
| `analysis/solicitations_*.py` | Trace technology contracts back to posted solicitations (`make solicitations`) |
| `analysis/build_dcas.py` | Rebuild the DCAS roster and its attributed payments (`make dcas`) |
| `analysis/verify_claims.py` | Recomputes every published figure; fails loudly on drift |
| `analysis/drift_report.py` | The same checks, reporting movement instead of failing on it (`make drift`) |
| `analysis/build_dashboard_data.py` | Rebuild the data behind the dashboard (`make dashboard`) |
| `scripts/weekly_refresh.sh` | The weekly chain: pull, classify, rebuild, report drift, rebuild the dashboard |
| `scripts/install_schedule.sh` | Install that chain as a launchd job, from the plist template beside it |
| `docs/SCHEDULING.md` | What the weekly run does, what it deliberately skips, and what will bite |
| `tests/` | Hand-read labels and the scripts that score against them |

## Things that will bite you

Found by testing the API against itself. Each is handled in code rather than
left as a comment.

**The fiscal-year filter on contract search does nothing.** Send `FiscalYear`
and the server returns the entire contract universe, identical for every year,
while reporting `filters: []`. The same is true of `Keyword` on solicitations.
`DCProcurementClient.search` compares the filters the server echoes against what
was sent and raises rather than hand back a wider slice than requested.

**`paymentAmount` is not the amount of that payment.** It is the total of the
whole check or ACH run the voucher was paid in, repeated on every row in that
run. Summing it gives $114.7B against $33.5B of actual vouchers, and made one
contract with a $120M ceiling look like it had been paid $17.4B.
`load_payments` returns `voucherAmount` as `amount` and keeps the other as
`check_total` so it cannot be summed by accident.

**A contract search row is one option period, not one contract.** 7,169 rows
are 4,013 contracts. Summing them gives a lifetime ceiling, which is neither an
annual figure nor money spent.

**Ceilings and payments cover different windows.** Option periods run from
October 2020 to 2031; payments reach back to FY2017. Comparing them without
windowing counts $11.9B of payments made before the ceilings existed.

**Vendor names are not standardised.** CDW Government appears three ways in the
technology payments alone. `vendor_aliases.yaml` records the rules, what they
merge, and the pairs left separate on purpose.

**Most payments cannot be tied to a contract.** Of 869,839 rows, 481,031 carry
no contract number, covering $17.8B. That is a ceiling on any share-of-spend
analysis built by joining payments to contracts.

**NIGP coding is noisy in both directions.** Treat any single-class total as
approximate.

**The solicitations list hides its own solicitation numbers.** 96% of rows show
an internal `Doc######` ID where the procurement number should be. The ID turns
out to be the join key — award documents print it in their "Solicitation
Number" box — and some rows carry stray control characters in numbers and
titles.

## What this does not cover

Independent agencies procure separately and do not appear in the portal.

The portal has no record type for task orders issued under ordering vehicles.
Some schedule buying surfaces as purchase orders or later contracts, but nothing
links those back to their vehicle. Schedule vehicles carry the largest ceilings
in the technology set, so the biggest single channel of technology buying cannot
be followed. That is the largest gap.

Delivery structure could be read for 387 of 480 candidate contracts: 313 with
a native text layer, 74 recovered from scans and extraction failures by
vision-model OCR (`make ocr`). The remaining 93 have no award document
attached at all.

The portal is live and counts move between pulls, so `data/raw/manifest.json`
records the date of the snapshot every figure was computed from.
