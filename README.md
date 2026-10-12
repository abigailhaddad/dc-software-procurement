# How the District buys software

Pulls the DC Contracts and Procurement Transparency Portal, sorts every contract
by what it actually buys, and reads how the work is structured out of the award
PDFs.

The findings are on the briefing, `site/index.html`: open it in a browser, and
the contracts behind each figure are a click away. Every number on it is
recomputed by `analysis/verify_claims.py`, which fails if one has drifted. This
file states no figures on purpose -- they would go stale with the next pull. It
is about the code: how to run it, why contracts are sorted by reading them, and
what will bite someone working on it. Some findings rest on a model reading
contract text, and those carry a measured error rate; `docs/CLASSIFIER_ACCURACY.md`
says which. `docs/NEXT_STEPS.md` is what remains.

## Run it

```bash
make install
make hydrate     # fetch the archived snapshot into data/raw
make pull        # refresh it from the portal
make classify    # needs OPENAI_API_KEY in .env
make verify      # recomputes every published figure, fails on drift
make test        # scores the classifiers against hand-read labels
```

`make help` lists the rest. Every step is resumable: pulls merge into what is
already on disk and classification is cached by content, so rerunning costs
nothing.

## The portal forgets; the archive does not

The portal is a rolling window, not an archive. When a fiscal year turns over it
stops listing older contracts, and the oldest option periods of contracts it
still lists. So a pull never replaces what is on disk: it merges, and a row the
portal stops returning is kept, marked `listed: False` with the last date it was
seen. Payments and purchase orders merge on groups -- a revised payment comes
back changed under the same id, a revised order under a new `-Vn` number -- so
the old version is not kept beside its replacement and counted twice.
`src/dcprocure/archive.py` has the detail.

`data/raw` is not tracked in git. `make publish` uploads it to `archive/` in the
public Hugging Face dataset `abigailhaddad/dc-procurement-raw`, and refuses to
replace a file with one that has fewer rows. `make hydrate` brings it back, and
`make prune` deletes the local copy, but only if every file matches the
published one byte for byte. Rows lost before the archive existed were put back
with `dcprocure recover-delisted`: the detail endpoint still answers for a
delisted contract, and a contract id is `number¦period` encoded as `base64`.

## Refreshing the payments table

`make pull` fetches everything, including the full payments table in a single
large response. The payments module ignores every filter it is given, so there
is no way to pull a slice of it: the pull instead checks that what came back is
the whole table, and refuses to overwrite the snapshot if it is not. To refresh
just payments without re-pulling contracts and solicitations, run
`make pull-payments`.

## Drift: what the weekly refresh reports

`make verify` recomputes every published figure from the snapshot and fails if
one no longer holds. The asserted figures in `analysis/verify_claims.py` are the
published baseline -- what the briefing currently says -- and changing one is a
decision made after looking at why it moved.

The portal is live, so those figures drift. `make drift` runs the same checks and
reports the movement instead of failing on it: how many claims were checked, how
many moved, and for each one the published value, the current value and the
change. Reports land in `data/drift/`, named for the snapshot date in
`data/raw/manifest.json`, with `latest.txt` and `latest.json` pointing at the most
recent run. It exits zero whether or not anything moved, and non-zero only when
the run itself breaks.

`scripts/weekly_refresh.sh` chains those together -- hydrate, pull, publish,
classify, rebuild the derived tables, report drift, rebuild the dashboard, prune
-- and `scripts/install_schedule.sh` installs it as a weekly launchd job.
`site/dashboard.html` is in the chain because it reports the current snapshot;
`site/index.html` is not, because the briefing stays on the published baseline.
Nothing in that chain rewrites the briefing: republishing is a human decision
made after reading the drift report. `docs/SCHEDULING.md` has the details.

## Why the commodity codes are not used

They are assigned by the buyer and applied loosely. Class 918, "consulting
services", holds Medicaid managed care contracts worth billions. Class 920,
"data processing, computer, programming and software services", holds a plumbing
contractor's contract. The District's largest IT staffing vehicles -- including
one titled "Information Technology Staff Augmentation Services" -- are filed
under 961/962, "miscellaneous services, not otherwise classified", while the
purchase orders written against them are coded 918.

So contracts are sorted by reading them. `classifier_config.yaml` holds the
schema and prompt for what a contract buys; `delivery_config.yaml` does the same
for how the work is structured. Accuracy is scored against contracts labelled by
hand in `tests/`, blind, and `docs/CLASSIFIER_ACCURACY.md` has the measurements
and what they were run on. Delivery structure agrees with the hand labels well
below the rate at which two human readers agree with each other, which is why
no delivery-structure breakdown is published. The model's misses on what agile
language is doing are genuine commitments it filed as incidental; they are
corrected by hand in `delivery_overrides.yaml`, with reasons, and the eval
expects exactly those.

One work-type miss is not corrected anywhere. "DOH FY25 Nurse Staffing Temporary
Support Services" is nurse staffing, and the model files it as technology staff
augmentation, because "temporary support services" reads as staff augmentation
whatever the staff do. It is in the eval because it was caught in the wild.
Nothing overrides work_type, so that inflation stands in the published numbers
until the taxonomy or the prompt distinguishes staffing a hospital from staffing
a help desk.

## Layout

| Path | What it does |
| --- | --- |
| `src/dcprocure/client.py` | The portal API, with guards against its silent-filter behaviour |
| `src/dcprocure/pull.py` | Fetch raw JSON into `data/raw/`, merging with what is there, with a provenance manifest |
| `src/dcprocure/archive.py` | Keep what the portal stops listing; recover what it already dropped |
| `src/dcprocure/publish.py` | Publish `data/raw` to the Hugging Face archive, fetch it back, prune the local copy |
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
| `analysis/pull_h1b_dol.py` | Filter DOL's own LCA disclosure files to the vendor set -- case status, worksite address, and the client-naming secondary-entity field |
| `analysis/extract_rates.py` | Pull hourly rate cards out of the award texts, every rate verified verbatim |
| `data/labor_rates.csv` | The published rates, with whether each title-to-price pairing verified |
| `analysis/modifications.py` | Count and read contract modifications (`make modifications`); the deduplication trap is in its docstring |
| `analysis/competition.py` | Read how competitively technology contracts were awarded, out of the D&Fs (`make competition`) |
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
| `analysis/verify_claims.py` | Recomputes every published figure; fails loudly on drift |
| `analysis/drift_report.py` | The same checks, reporting movement instead of failing on it (`make drift`) |
| `analysis/build_dashboard_data.py` | Rebuild the data behind the dashboard (`make dashboard`) |
| `scripts/weekly_refresh.sh` | The weekly chain: hydrate, pull, publish, classify, rebuild, report drift, rebuild the dashboard, prune |
| `scripts/install_schedule.sh` | Install that chain as a launchd job, from the plist template beside it |
| `docs/SCHEDULING.md` | What the weekly run does, what it deliberately skips, and what will bite |
| `tests/` | Hand-read labels and the scripts that score against them |

## Things that will bite you

**Payments joined to contracts do not make a time series.** The portal's
contracts table is a current-state snapshot: contracts that ended before this
project began are not in it. Joining payments to it and plotting by fiscal year
shows technology spending rising steeply, and the rise is entirely an artifact:
the share of payment dollars matching any classified contract climbs year by
year, so the series measures how much of the past the portal still remembers.
The published five-year figures are safe because a recent window is where the
join works. Nothing built that way should be extended backwards. Use the
commodity codes on the purchase orders instead, which reach back further with no
comparable decay.

**A pull made after the fiscal year turns over carries a stub of the new year.**
Anything that groups payments by fiscal year has to say which years it covers
rather than read the range off the data, or a few days of a new year turn up as
a year of their own. `analysis/trends.py` pins its decade for this reason.

**Vendor names in the payments table are truncated.** Most are cut at a fixed
length, which defeats the suffix-stripping in `vendor_aliases.yaml`: "CARAHSOFT
TECHNOLOGY CORP" loses its suffix and "CARAHSOFT TECHNOLOGY CORPORATI" does not,
so one company becomes two keys. Repairing it moves the published top-ten share
a little -- not enough to change the published figures, enough to matter to
anything finer.

Found by testing the API against itself. Each is handled in code rather than
left as a comment.

**The fiscal-year filter on contract search does nothing.** Send `FiscalYear`
and the server returns the entire contract universe, identical for every year,
while reporting `filters: []`. The same is true of `Keyword` on solicitations.
`DCProcurementClient.search` compares the filters the server echoes against what
was sent and raises rather than hand back a wider slice than requested.

**`paymentAmount` is not the amount of that payment.** It is the total of the
whole check or ACH run the voucher was paid in, repeated on every row in that
run. Summing it gives several times the real total, and makes a single contract
look as if it had been paid more than the District's whole technology budget.
`load_payments` returns `voucherAmount` as `amount` and keeps the other as
`check_total` so it cannot be summed by accident.

**Payment ids are not unique.** Several rows can share one. Do not de-duplicate
on `id`.

**A contract search row is one option period, not one contract.** Summing the
rows for a contract gives a lifetime ceiling, which is neither an annual figure
nor money spent.

**Ceilings and payments cover different windows.** Payments reach back years
before the earliest option period on the portal. Comparing them without
windowing counts payments made before the ceilings existed.

**Vendor names are not standardised.** One vendor can appear under many
spellings in the technology payments alone. `vendor_aliases.yaml` records the
rules, what they merge, and the pairs left separate on purpose.

**Most payments cannot be tied to a contract.** Most payment rows carry no
contract number at all. That is a ceiling on any share-of-spend analysis built
by joining payments to contracts.

**Document ids change.** The portal sometimes re-files a document under a new id,
and every text cache here is keyed by id, so a re-filed document reads as never
opened. `docs/NEXT_STEPS.md` has the open fix.

**NIGP coding is noisy in both directions.** Treat any single-class total as
approximate.

**The solicitations list hides its own solicitation numbers.** Nearly every row
shows an internal `Doc######` ID where the procurement number should be. The ID
turns out to be the join key -- award documents print it in their "Solicitation
Number" box -- and some rows carry stray control characters in numbers and
titles.

## What this does not cover

Independent agencies procure separately and do not appear in the portal.

The portal has no record type for task orders issued under ordering vehicles,
and a task order is filed as its own contract with the parent named only inside
the PDF. Purchase orders can be traced, though: a payment line carries both a PO
number and a contract number, so orders resolve to the contract they were booked
against, and no order resolves to two. That link runs through the payments table
rather than the order table, which is why it looked absent.

It does not fill the gap. Almost all of the ordering that can be traced to a
technology ordering vehicle goes through a single one, OCTO's STaR II, and most
vehicles show no traced order at all. Either the ordering is invisible or it is
not happening, and the data does not say which.

Matching orders to vehicles by vendor name instead was tried and fails. The
rise in a holder's orders after its vehicle starts sits inside the range seen
for vendors who never held one, so it measures a relationship that already
existed rather than the vehicle. `analysis/vehicle_orders.py` reports the
comparison.

Delivery structure cannot be read for every candidate contract: some award
documents are scans, recovered by vision-model OCR (`make ocr`), and some
contracts have no award document attached at all.

The portal is live and counts move between pulls, so `data/raw/manifest.json`
records the date of the snapshot every figure was computed from.
