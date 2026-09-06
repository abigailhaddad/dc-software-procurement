# How the District buys software

> **Read the caveats with the numbers.** Every figure here is computed from the
> published data: `analysis/build_readme.py` renders this file from
> `README.md.in`, and `analysis/verify_claims.py` fails if re-rendering would
> change it. Some rest on a model reading contract text rather than on the
> District's own records, and those carry a measured error rate:
> `docs/CLASSIFIER_ACCURACY.md` says which, and how far off each is.
> `docs/NEXT_STEPS.md` is what remains.

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

The findings are on the briefing rather than in this file: what the District
buys, what it pays, what the award documents say about how competitively it was
awarded, and what ten years of payments show. The rest of this file is about the
code -- how to run it, why contracts are sorted by reading them, and what will
bite someone working on it.

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
| `analysis/solicitations_cycle_time.py` | Days from a solicitation closing to the award it produced (`make cycle-time`) |
| `analysis/trends.py` | Ten fiscal years of payments: growth, concentration, incumbency (`make trends`) |
| `analysis/vehicle_orders.py` | Trace purchase orders back to the ordering vehicle they were placed against (`make vehicles`) |
| `analysis/technology_buyers.py` | Who buys technology, read three ways: contracts, payments, purchase orders (`make buyers`) |
| `README.md.in` | This file with every figure replaced by a named placeholder; the rendered README is generated from it |
| `analysis/build_readme.py` | Compute every figure the README states and render it (`make readme`) |
| `analysis/verify_claims.py` | Recomputes every published figure; fails loudly on drift |
| `analysis/drift_report.py` | The same checks, reporting movement instead of failing on it (`make drift`) |
| `analysis/build_dashboard_data.py` | Rebuild the data behind the dashboard (`make dashboard`) |
| `scripts/weekly_refresh.sh` | The weekly chain: pull, classify, rebuild, report drift, rebuild the dashboard |
| `scripts/install_schedule.sh` | Install that chain as a launchd job, from the plist template beside it |
| `docs/SCHEDULING.md` | What the weekly run does, what it deliberately skips, and what will bite |
| `tests/` | Hand-read labels and the scripts that score against them |

## Things that will bite you

**Payments joined to contracts do not make a time series.** The contracts table
is a current-state snapshot: contracts that ended are not in it. Joining
payments to it and plotting by fiscal year gives technology spending rising from
$3.2M to $199M, a 62-fold increase that is entirely an artifact -- the share of
payment dollars matching any classified contract climbs from 0.5% in FY2017 to
13.6% in FY2026, so the series measures how much of the past the portal still
remembers. The published FY2021-2025 figures are safe because a recent window is
where the join works. Nothing built that way should be extended backwards. Use
the commodity codes on the purchase orders instead, which reach FY2013 with no
comparable decay.

**Vendor names in the payments table are truncated at 30 characters.** 73,061
rows are exactly 30 characters long against 2,073 at 31. That defeats the
suffix-stripping in `vendor_aliases.yaml`: "CARAHSOFT TECHNOLOGY CORP" loses its
suffix and "CARAHSOFT TECHNOLOGY CORPORATI" does not, so one company becomes two
keys. Repairing it moves the published top-ten share from 52.5% to 53.2%, small
enough that the published figures stand, large enough to matter to anything
finer.


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

The portal has no record type for task orders issued under ordering vehicles,
and a task order is filed as its own contract with the parent named only inside
the PDF. Purchase orders can be traced, though: a payment line carries both a PO
number and a contract number, so 103,990 orders resolve to the contract they
were booked against, and no order resolves to two. That link runs through the
payments table rather than the order table, which is why it looked absent.

It does not fill the gap. Of FY2021-2025 IT-class orders, $124.8M traces to one
of the 330 technology ordering vehicles, and $121.9M of that -- 97.7% -- is a
single vehicle: OCTO's STaR II, drawn on by 28 agencies. 309 of the 330 show no
traced order at all, and 143 holding $388.2M of ceiling have a vendor who placed
no IT order of any kind after the vehicle started. Either the ordering is
invisible or it is not happening, and the data does not say which.

Matching orders to vehicles by vendor name instead was tried and fails. The
rise in a holder's orders after its vehicle starts sits inside the range seen
for vendors who never held one, so it measures a relationship that already
existed rather than the vehicle. `analysis/vehicle_orders.py` reports the
comparison.

Delivery structure could be read for 387 of 480 candidate contracts: 313 with
a native text layer, 74 recovered from scans and extraction failures by
vision-model OCR (`make ocr`). The remaining 93 have no award document
attached at all.

The portal is live and counts move between pulls, so `data/raw/manifest.json`
records the date of the snapshot every figure was computed from.
