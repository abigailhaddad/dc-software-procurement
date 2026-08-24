# DC procurement: technology and custom software

Pulls the District of Columbia's contracting data and analyzes what DC buys when
it buys software — who the vendors are, how much it spends, and how the work is
billed.

Data comes from the [Contracts and Procurement Transparency
Portal](https://contracts.ocp.dc.gov), which publishes contracts, purchase
orders, payments, and solicitations for DC agencies that buy through the Office
of Contracting and Procurement. The portal's Angular front end is backed by an
undocumented public JSON API; `dcprocure.client` wraps it.

## Quick start

```bash
uv sync
uv run python -c "from dcprocure.pull import pull_all; pull_all()"
```

That writes raw JSON plus a `manifest.json` recording the pull date into
`data/raw/`. Analysis reads from those files, never from the network, so any
number can be re-derived from the same snapshot.

## Layout

| Module | What it does |
| --- | --- |
| `client.py` | The portal API, including guards against its silent-filter behavior |
| `commodities.py` | Which NIGP commodity classes count as "technology", and why |
| `pull.py` | Fetch raw JSON to `data/raw/` with a provenance manifest |
| `parse.py` | Normalize money, dates, fiscal years; derive pricing model |
| `documents.py` | Download award PDFs and read the billing terms out of them |
| `classify.py` | LLM classification of what each contract actually buys |
| `parse.normalize_vendor` | Vendor identity, with the rules in `vendor_aliases.yaml` |
| `analysis.py` | Summary tables |

Prompt and schema both live in `classifier_config.yaml`; the labelled test set
lives in `tests/gold_labels.csv`. Vendor identity decisions live in
`vendor_aliases.yaml` — which spellings count as one company, which pairs are
deliberately kept apart, and a regenerated record of what the rules actually
merge. `verify_claims.py` fails if that record drifts from what the rules do.

## The API

No authentication. Base URL `https://contracts.ocp.dc.gov`.

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/api/{module}/search` | POST | `{"FilterBy": [{"name", "value"}], "OrderBy": []}` |
| `/api/contracts/details?id=` | GET | Only source of `contractType` and attachments |
| `/api/contracts/commodities` | GET | 266 three-digit NIGP classes |
| `/api/contracts/agencies` | GET | Agency clusters and members |
| `/contracts/attachments/{id}` | GET | The contract PDF. Note: no `/api` prefix |

Modules are `contracts`, `purchase`, `payments`, `solicitations`, `forecast`.

### Things that will bite you

These were all found by testing the API against itself, and each one is handled
in code rather than left as a comment.

**The fiscal-year filter on contract search does nothing.** Send
`FiscalYear` and the server returns the entire 7,271-row contract universe,
identical for every year, while reporting `filters: []`. The same is true of
`Keyword` on solicitations. `DCProcurementClient.search` compares the filters
the server echoes against what was sent and raises rather than hand back a
wider slice than requested.

**A contract search row is one option period, not one contract.** The 7,271
rows are 4,087 contracts; a five-year contract appears up to six times, each row
carrying that period's amount. Sum the rows and you get a lifetime ceiling, which
is not an annual figure and not money spent.

**`contractType` mixes pricing with vehicle type.** "Firm Fixed Price" and
"Labor Hour" describe billing. "Indefinite Delivery Indefinite Quantity" and
"Requirements" describe an ordering vehicle whose task-order pricing the portal
never exposes — and they cover about 65% of software-services dollars. `parse.py`
keeps them in a separate `set_at_task_order` bucket instead of guessing.

**NIGP classes 918 and 958 are not technology.** "Consulting services" and
"management services" look like natural additions to an IT total. They are
dominated by Medicaid managed care contracts — several at $8.8B — and including
them overstates DC technology spend by roughly ten times.

**Never sum `paymentAmount`.** It is not the amount of that payment. It is the
total of the whole check or ACH run the voucher was paid in, repeated
identically on every row belonging to that run. Summing it inflates totals by
roughly 3.4x across the table ($113.7B against $33.3B of actual vouchers), and
concentrates the error wherever a vendor is paid in large batched runs: OCTO
STaR II, a staffing contract with a $120M ceiling, appears to have been paid
$17.4 billion — more than the District's entire annual budget. `voucherAmount`
is the line-level figure. `parse.load_payments` returns it as `amount` and keeps
the other as `check_total` so it cannot be summed by accident.

**Most payments cannot be tied to a contract at all.** Of 866,823 payment rows,
478,668 ($17.8B) carry no contract number. That is a ceiling on any
share-of-spend analysis built by joining payments to contracts, and it is a
property of the data rather than something more classification can fix.

**The biggest IT services contracts are not in any IT commodity class.** DC's
IT staff-augmentation vehicles are coded 961/962, "miscellaneous services, not
otherwise classified" — including OCTO STaR II at a $120M ceiling and a contract
literally titled "Information Technology Staff Augmentation Services" at $65M.
The coding is not even self-consistent within one vendor: Computer Aid, Inc.'s
*contracts* sit in class 962 while the *purchase orders* against them are coded
918. An IT total built from IT commodity classes alone misses all of it, so
`pull.py` pulls the miscellaneous classes too and `parse.py` groups them
separately for title-level filtering.

**NIGP coding is self-assigned and noisy.** Class 920, "data processing,
computer, programming, and software services", contains purchase orders to a
plumbing contractor. Treat any single-class total as approximate.

**Other API quirks.** There is no pagination; every search returns its full
result set, and the payments module will hand back ~867,000 rows in one
response. Requests without a browser-like `User-Agent` get a 403.
`CommodityCode` takes a three-digit class while result rows carry seven-digit
codes. Solicitation `Type` is a required numeric enum (0 open, 1 archived) and
is rejected without an accompanying `Status` or `ArchivedFiscalYear`.

## What the data shows

DC does comparatively little discrete custom-software procurement. Of roughly
$955M in class-920 contract ceilings, most is not project work:

* **The DC Supply Schedule (DCSS)** accounts for the largest block — a dozen-odd
  near-identical "Information Technology Equipment and Software" IDIQ vehicles at
  $10–40M ceilings each, held by small set-aside vendors. These are ordering
  vehicles, not projects; the actual work only becomes visible as task orders,
  which the portal does not publish. About 23% of technology solicitations are
  restricted to schedule holders.
* **License resale** is much of the rest, chiefly Microsoft Enterprise Agreements
  bought through CDW ($90M and $31M ceilings).
* **Genuine custom systems are few and long-lived** — DCAS (health benefits
  eligibility, $83.7M for one O&M award), UIBS (unemployment, Geographic
  Solutions), the FAST Enterprises tax systems. These are procured as operations
  and maintenance of an existing system far more often than as a build.
* **Labor is bought separately from software**, through staffing vehicles coded
  outside IT entirely. Computer Aid, Inc. alone runs ~1,200 purchase orders a
  year at a $56K median across eight agencies, a shape that reads as one
  resource-period per order. Compare FAST Enterprises: eight orders, $2.8M
  median.

Only about seven of 12,273 solicitations from FY2016–FY2026 read as custom
software builds, and most of those are false positives on the word "system"
(HVAC, a vehicle wash). Open competition for new software is rare; competition
mostly happens at task-order level among pre-qualified schedule holders.

## Classifying the work

Commodity codes cannot identify DC technology spending, so the classification is
done on contract titles by an LLM. `classifier_config.yaml` is the single source
of truth: its `fields:` block builds both the Pydantic schema sent to the model
and the prompt text, following the same convention as the
generic-comment-analyzer project. Model defaults to `gpt-5.4-nano`, overridable
with `LLM_MODEL`.

```bash
uv run python analysis/classify_contracts.py   # labels every contract
uv run python tests/eval_labels.py             # scores against gold labels
```

Each contract gets a `work_type` — the distinction that matters most being
between paying for a deliverable (`custom_software_build`, `cots_implementation`,
`software_om_support`) and paying for people (`staff_augmentation`) — plus a
`buys_people_not_deliverables` flag and any named system. `ordering_vehicle` is
a third thing again: a schedule contract that authorizes future orders without
buying anything itself.

`tests/gold_labels.csv` holds 20 hand-labelled contracts, chosen because they
are the cases commodity codes get wrong in both directions: Medicaid managed
care and food service sitting in the consulting class, a plumbing contractor in
the software class, IT staff augmentation under miscellaneous services. Where two
labels are genuinely defensible the gold column holds both, separated by a pipe.
Every row carries a note saying why it is there, so a config edit that breaks one
is readable rather than mysterious. Current accuracy is 20/20.

One documented blind spot: contracts whose title is a bare acronym. "OCTO STaR
II" is the District's largest IT labour vehicle, but nothing in the title says
so, and calling it staffing requires outside knowledge the classifier does not
have.

Results are cached by model plus a fingerprint of the config, so editing the
prompt invalidates stale labels instead of silently reusing them.

## Measurement choices

Annual spend comes from **purchase orders**, which carry a real fiscal year and
represent money committed. Contract amounts are authorization ceilings and are
used only for structure — vendor concentration, contract type, market type.

Billing model comes from the **award PDFs**, not from `contractType`. DC
contracts state their type in standardized language drawn from 27 DCMR Chapter
24 ("The District contemplates award of a firm fixed price contract..."), and
price schedules that list labor categories with hourly rates identify hours-based
work regardless of what the vehicle is called. Some awards are scanned images
with no text layer; those are reported as `needs_ocr` rather than assumed.
