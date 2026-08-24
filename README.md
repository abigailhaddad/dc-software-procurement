# How the District buys software

Pulls the DC Contracts and Procurement Transparency Portal, sorts every contract
by what it actually buys, and reads how the work is structured out of the award
PDFs.

The briefing built from this is `analysis/how_dc_buys_software.ipynb`. Every
figure in it comes from a cell you can rerun. `site/index.html` is the same
briefing as one static page: open it in a browser, and the contracts behind
each figure are a click away.

## Run it

```bash
make install
make pull        # ~10 min, writes ~320MB into data/raw
make classify    # needs OPENAI_API_KEY in .env
make verify      # recomputes every published figure, fails on drift
make test        # scores the classifiers against hand-read labels
```

`make help` lists the rest. Every step is resumable: pulls skip what is already
on disk and classification is cached by content, so rerunning costs nothing.

## What it found

Of 1,372 technology contracts carrying $4.6B in ceilings, custom software
development accounts for 60 contracts and $38M. That is a floor on custom
development in contract form; building also happens inside the $1.2B of staff
augmentation and inside task orders this data cannot see. Ordering vehicles
carry $1.8B. Half of the $579M in traceable technology payments went to ten
vendors, and 17% to one — measured on the 44% of contracts whose payments carry
their number, though the top vendor is corroborated separately by the
purchase-order table. 42 award documents publish hourly rate cards — 2,146
not-to-exceed rates, $24.01 to $1,015 — and job ads recruiting for the same
tier names show what the workers are offered against them.

Among contracts that build or run something, the largest group fixes its scope
before work starts: 74 of 82 sequential, gated contracts cannot change what is
built without a contract modification.

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
hand in `tests/`: 20 of 20 on what is being bought, 20 of 20 on delivery
structure, 18 of 20 on what agile language is doing. The two misses are genuine
commitments the model filed as incidental; they are corrected by hand in
`delivery_overrides.yaml`, with reasons, and the eval expects exactly those two.

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
| `site/index.html` | The briefing as a single static page |
| `analysis/rebuild_manifest.py` | Rebuild the provenance manifest from the files on disk |
| `analysis/extract_rates.py` | Pull hourly rate cards out of the award texts, every rate verified verbatim |
| `data/labor_rates.csv` | The 2,146 published rates, with whether each title-to-price pairing verified |
| `analysis/build_site_data.py` | Rebuild the drill-down data behind the site (`make site`) |
| `evidence/job_ads/` | Job postings quoted in the briefing: screenshots, raw pages, archive links |
| `analysis/verify_claims.py` | Recomputes every published figure; fails loudly on drift |
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
run. Summing it gives $113.7B against $33.3B of actual vouchers, and made one
contract with a $120M ceiling look like it had been paid $17.3B.
`load_payments` returns `voucherAmount` as `amount` and keeps the other as
`check_total` so it cannot be summed by accident.

**A contract search row is one option period, not one contract.** 7,113 rows
are 3,983 contracts. Summing them gives a lifetime ceiling, which is neither an
annual figure nor money spent.

**Ceilings and payments cover different windows.** Option periods run from
October 2020 to 2031; payments reach back to FY2017. Comparing them without
windowing counts $11.9B of payments made before the ceilings existed.

**Vendor names are not standardised.** CDW Government appears three ways in the
technology payments alone. `vendor_aliases.yaml` records the rules, what they
merge, and the pairs left separate on purpose.

**Most payments cannot be tied to a contract.** Of 866,823 rows, 478,716 carry
no contract number, covering $17.8B. That is a ceiling on any share-of-spend
analysis built by joining payments to contracts.

**NIGP coding is noisy in both directions.** Treat any single-class total as
approximate.

## What this does not cover

Independent agencies procure separately and do not appear in the portal.

The portal has no record type for task orders issued under ordering vehicles.
Some schedule buying surfaces as purchase orders or later contracts, but nothing
links those back to their vehicle. Schedule vehicles carry the largest ceilings
in the technology set, so the biggest single channel of technology buying cannot
be followed. That is the largest gap.

Delivery structure could be read for 288 of 476 candidate contracts. The rest
had no award document attached or a scanned one with no text layer.

The portal is live and counts move between pulls, so `data/raw/manifest.json`
records the date of the snapshot every figure was computed from.
