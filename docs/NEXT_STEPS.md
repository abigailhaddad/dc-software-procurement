# Where this stands, and what is left

Updated 2026-10-12. Written so someone with no memory of the work can pick it
up. Read `README.md` and `docs/SCHEDULING.md` first.

## The state

- **Site:** https://dc-procurement.pages.dev (Cloudflare Pages project
  `dc-procurement`). Static pages in `site/`: contracts explorer
  (`contracts.html`; `index.html` is the Start here page), `payments.html`, `purchase-orders.html`, one page per contract
  (`contract.html?n=…`), and `about.html`. Each explorer has charts on top and a
  table below, driven by one set of filters on the District's own fields
  (agency, vendor, contract type, NIGP code, market type, fiscal year). No
  classification, estimate or finding is on the site.
- **Data:** public Hugging Face dataset `abigailhaddad/dc-procurement-raw`.
  `archive/` holds the merged JSON and `archive/parquet/` the tables the site
  reads. Pulls merge rather than replace, so records DC's site stops returning
  are kept and flagged (`listed`, `first_listed`, `last_listed`).
- **Repo:** public, `abigailhaddad/dc-software-procurement`. Audited before it
  went public: no secrets, `.env` never committed.
- **Schedule:** `.github/workflows/weekly.yml` runs `scripts/weekly_refresh.sh`
  Mondays 10:00 UTC (hydrate, pull, export, verify, publish, prune). A local
  launchd job (`com.dcbudget.weekly`) also exists as a fallback.
- **Checks:** `make verify` (hydrate + export + `tests/test_export.py` +
  `tests/test_archive.py`); the pre-push hook runs it.

## Open tasks

1. **Confirm a clean weekly Actions run on current code.** Run 38286140587 was
   started before the latest wording changes. When it finishes, check it, then
   `gh workflow run weekly.yml --repo abigailhaddad/dc-software-procurement`
   and verify that run end to end (the published `columns.json` and
   `summary.json` should carry the "DC's site" wording).
2. **Then retire the launchd fallback** with `scripts/install_schedule.sh
   --remove`, only once Actions has run cleanly and with the owner's OK.
3. **Auto-deploy of the site** needs a `CLOUDFLARE_API_TOKEN` secret (and
   account id) in the repo. Until then `deploy-site.yml` skips, and deploys are
   by hand: `wrangler pages deploy site --project-name dc-procurement`.
   Publish new data files to HF *before* deploying site code that reads them.
4. **Optional custom domain** for the Pages project.
5. **Model classifications on the site (owner's call, 2026-10-12).** Showing
   what the model assigned is fine as long as (a) the site never aggregates by
   it: no chart, no totals, no breakdown; and (b) every place it appears says
   plainly that it comes from a model, not a District field. A sensible shape is
   a labelled field on `contract.html` (and possibly a table column), sourced
   from `data/contract_classifications.csv` (`work_type`, `is_technology`,
   `confidence`). Decide whether filtering on it counts as aggregating; it
   probably does, since filtered views show totals. Only `work_type` has
   accuracy behind it (below); the delivery-structure labels do not and should
   stay off the site. The classifications would need publishing to HF and
   exporting like the other tables, with the About page explaining the model,
   its config and the measured accuracy.

6. **Three option periods have no detail record** (DCKA-2024-C-0080,
   DCKA-2024-C-0116, DCRL-2022-C-0028), so their contract type, market type,
   contracting officer and documents are blank. DC's detail API returns at
   least the first. `pull_contract_details` logs a failed fetch and moves on;
   uncached ids are retried each pull, so these have likely failed more than
   once. The first id's contractNumber has a trailing space on DC's site.
7. **The forecast is not pulled.** `client.MODULES` lists "forecast", but
   nothing fetches it.
8. **The options count lives in `site/renewals-options.json`** (13 award
   documents read by hand). It would sit better as a root-level YAML exported
   like `of_interest.yaml`. Each entry applies only while DC's site shows the
   same period label and end date it was read against.

9. **Contract rows carry no `listed`, `first_listed` or `last_listed`.** In
   `contracts_by_commodity.json` the 6,065 current rows have none of these keys
   and the 1,297 kept from earlier pulls have only `listed: false`, though
   `retain_by_key` sets all three. Something rewrote the file without the merge
   (check `recover` in cli.py). The export treats a missing `listed` as true, so
   the site is right today, but no first-seen date exists for any contract.

## The exploratory analysis (not on the site, not in the weekly run)

`analysis/` and the `make` targets under `derived` (modifications, competition,
trends, vehicles, cycle-time, reseller, part-numbers) run by hand. Some comments
in that code refer to files that no longer exist. Treat its findings as leads
to re-derive, not results. `docs/CLASSIFIER_ACCURACY.md` records how the two
classifiers were measured.

**What the measurements say**

- **work_type classifier:** 95% on technology/not, 90% on the sub-label, from a
  40-contract random sample. Both binary errors over-counted technology.
- **Delivery-structure classifier:** 55% agreement with blind hand labels,
  run-to-run noise of about ±4 contracts. Not usable.
- **Contract-based totals miss whole agencies.** DC Public Schools and OCFO hold
  no contract in the pull while placing large IT purchase orders. Anything
  counted from contracts describes what DC's site publishes, not what the
  District buys.

**Questions already answered**

- OCR of scanned justifications: done for 75 documents at about a penny each.
  The remaining ~336 unread documents are responsibility and
  price-reasonableness determinations, which looked analytically empty.
- Matching purchase orders to vehicles by vendor name: inside placebo noise.
- Do solicitations name their eventual winner: essentially never (1 of 82); the
  apparent cases are incumbent products being renewed.
- Reseller price differences for the same part: unanswerable; part numbers
  almost never repeat across contracts.
- Competition vs price and vs cycle time: too few overlapping contracts.

**Worth doing if the analysis is picked up**

- Rebuild the delivery excerpt for contracts with no lettered sections (it falls
  back to cover pages for about half); target the price schedule. Two attempts
  are recorded in `docs/CLASSIFIER_ACCURACY.md`.
- Widen the hand-labelled gold set (`tests/hand_labels_delivery.csv`, protocol
  in `docs/LABELLING_PROTOCOL.md`) before trusting any accuracy number.
- Collapse the six delivery labels to fixed scope / capacity / neither.
- Look outside DC's site for independent agencies, e.g. DC Council contract
  approvals. Unverified as a source.

## Traps

- `contract_details.json` holds one record per option period, newest first.
  Collapsing by contract number loses award documents; use
  `documents.award_document_index()`.
- Payments joined to contracts do not make a time series: the contracts table is
  current state, so ended contracts are absent. Join payments to purchase orders
  on the base order number (strip `-Vn`).
- Payment ids are not unique, and purchase orders are versioned (`-V2`). The
  archive groups on id and on base PO number respectively, so revisions don't
  double-count.
- Only a small share of payment dollars sit on lines carrying a listed contract
  number, which is why payments and orders have their own pages.
- Payment vendor names are truncated at 30 characters.
- `is_technology` comes back from pandas as a numpy bool: use `bool(x)`, not
  `x is True`.
- Changing `classifier_config.yaml` or `delivery_config.yaml` invalidates the
  whole classification cache (the config fingerprint is in the key).
- DC's site sometimes re-files a document under a new id. Text caches are keyed
  by id, so a re-filed document reads as never opened. Nothing catches this yet.
- "OPC" is two agencies; `agency_names.yaml` must never merge them.
