# Where this stands, and what is left

Written 2026-09-06 at the end of a long session. It exists so the next person --
including a version of you with no memory of any of this -- can pick the work up
without reconstructing it. Read `docs/CLASSIFIER_ACCURACY.md` alongside it.

## The state in one paragraph

The pipeline now runs weekly on its own and reports how far the published
figures have drifted, without republishing them. The published briefing is
frozen on the 2026-08-24 snapshot and carries a work-in-progress banner. Two
classifiers were measured against blind hand labels for the first time: the one
behind most of the briefing is good, the one behind its headline claim is not.
That headline claim is being rewritten. A second, better-evidenced finding about
competition is being built out and has not been published yet.

## What is running

`com.dcbudget.weekly` runs `scripts/weekly_refresh.sh` on Mondays at 06:00 --
pull, classify, rebuild the derived tables, drift, dashboard. It does not touch
the briefing. Reports land in `data/drift/`, logs in `data/drift/logs/`, last 12
kept. `scripts/install_schedule.sh --remove` stops it. Details in
`docs/SCHEDULING.md`.

It needs OpenAI credit for newly-appeared contracts. Roughly $7 remained at the
time of writing. If the key runs dry the classify step fails, the run reports it
and exits non-zero, and nothing silently serves stale numbers.

## What is settled

**The delivery-structure findings are not publishable.** 55% agreement with
blind hand labels, ±4 of run-to-run noise, and a modification record that
contradicts the interpretation. "74 of 82 sequential, gated contracts fix scope
before work starts" is being replaced. See `docs/CLASSIFIER_ACCURACY.md`.

**The work_type findings are usable with an error bar.** 95% on the
technology/not binary, 90% on the sub-label, from a 40-contract random sample.
Both binary errors inflated the technology count and none deflated it, so
"1,372 technology contracts" is probably slightly high rather than uncertain in
both directions.

**Portal arithmetic is solid.** 29 of the 198 published claims involve no model
at all -- corpus counts, the payments arithmetic, the ceiling-versus-drawdown
windows, the `paymentAmount` versus `voucherAmount` trap. Nothing found this
session touches them.

## What is in flight

Two agents were running when this was written. Check whether their work landed
before starting either:

1. **The delivery-section rewrite.** `analysis/modifications.py` to make the
   modification finding reproducible, then the prose in `README.md` and
   `site/index.html`, then the matching literals in `analysis/verify_claims.py`.
2. **The competition analysis.** `analysis/competition.py`, classifying D&F
   documents by their own header text rather than by filename.

## What is worth doing next

**Publish the competition finding.** Roughly 104 technology contracts, $216M of
ceiling, carry a signed justification for limiting or avoiding competition, and
64 of them are labelled `Open` in the portal's own `marketType` field. The
briefing says nothing about competition; there is no field in the dataset that
records it. This is the same shape as the commodity-code finding the project
already tells well, and unlike the delivery section it rests on documents that
say what they mean. Roughly 47% of the relevant D&Fs are image-only scans,
concentrated in the sole-source and price-reasonableness buckets, so OCR cost is
the blocker.

**Rebuild the excerpt for contracts with no lettered sections.** `delivery_excerpt`
shows the model a median 10.4% of the award text, and for 140 of 292 contracts
it finds neither a Section B nor a Section F heading and falls back to the first
9,000 characters -- the cover pages. Those are task orders and GSA-schedule
orders that do not use DC's form. Target the price schedule: that is what the
hand labellers actually used. Two attempted fixes both scored worse and are
recorded in `docs/CLASSIFIER_ACCURACY.md`, so read that before trying a third.

**Widen the gold set before trusting any accuracy number.** The random sample is
29 contracts and its 95% interval runs 37-73%. `tests/hand_labels_delivery.csv`
holds 80 labels with the passage each rests on; `/tmp/label_brief.md` was the
labelling protocol, and it is worth re-creating in the repo if this continues.

**Reconsider the six delivery labels.** Two careful readers agree 83%. The
scheme has no cell for a one-time build plus the operations of the thing built,
which is common here, and no way to say "half build, half hosting". Collapsing
it -- fixed scope, capacity, neither -- would likely raise both the ceiling and
the classifier.

**The notebook.** `analysis/how_dc_buys_software.ipynb` still contains the old
delivery-structure prose. The rewrite covered README and the site. The notebook
needs the same treatment and then re-execution.

## Traps that have already cost time

- `is_technology` comes back from pandas as a numpy bool. `x is True` is False.
  Use `bool(x)`. This has caused three separate wrong answers.
- `contract_details.json` holds one record per contract *option period*.
  Collapsing it with `{d["contractNumber"]: d ...}` loses award documents filed
  under other periods -- 199 contracts' worth. Use
  `documents.award_document_index()`.
- The classification cache key deliberately excludes the contract amount.
  Putting it back means every option-period exercise re-judges the contract.
- `data/pdftext/` is gitignored and `data/ratecache/` is tracked on purpose: the
  rule is what a rebuild costs, not what kind of file it is.
- Changing `classifier_config.yaml` or `delivery_config.yaml` at all invalidates
  the whole classification cache, because the config fingerprint is in the key.
- `make verify` failing is not necessarily a bug. It fails when current data has
  moved past the published baseline, which is the design. `make drift` reports
  the same comparison without failing.

## Things deliberately left alone

- The published briefing's figures, outside the delivery section. They are
  frozen until someone decides to restate them; the drift report exists to make
  that decision informed.
- Majority-of-3 voting in `src/dcprocure/classify.py`. Implemented, defaults
  off, benefit never measured because credit ran out mid-experiment.
- OCR at scale for D&Fs and modifications. The path exists in
  `analysis/ocr_awards.py`; nobody has priced it.
- `data/solicitations/profile.md`, a hand-written note pinned to August that no
  script regenerates. It now says so at the top.

## Data that lives elsewhere

The 2026-08-24 raw snapshot the briefing was computed from cannot be re-fetched
-- the portal serves current state only -- so it is on Hugging Face at
`abigailhaddad/dc-procurement-raw`, public, alongside the September pull and the
source documents. Only raw source material is published there; nothing the model
or the OCR produced. `docs/SCHEDULING.md` has the command to pull it back.
