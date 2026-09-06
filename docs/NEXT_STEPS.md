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

## What landed since

**The delivery section is rewritten.** "74 of 82" is withdrawn from README and
site/index.html, and the verify_claims literals behind it are gone. What stands
in its place: 25 of 80 hand-read award documents describe a delivery process,
and build contracts whose award fixes scope average 6.5 modifications against
4.7 for the rest, which is the opposite of what the old claim implied.
`analysis/modifications.py` (`make modifications`) makes that reproducible.

**The competition finding is published.** `analysis/competition.py` writes
`data/competition/`; the finding is in README.md and site/index.html, and
verify_claims checks it. 51 technology contracts, $45.5M, carry a D&F limiting
competition and 31 are labelled `Open`; six cite PPRA 2-354.13(8) vendor
lock-in. The portal's `marketType` can say Sole Source -- 24 contracts corpus-wide
do -- and no technology contract ever does.

**The District's vocabulary is in `procurement_vocabulary.yaml`**, not compiled
into Python: the five spellings of sole source, the D&F header phrases, the
statute table. Parsing mechanics stayed in code. The file is deliberately
outside the classifier cache fingerprint, so editing it cannot invalidate the
4,013 cached labels.

**Both published files' prose is checked.** verify_claims string-checks
site/index.html as well as README.md, and the drift report groups stale
sentences by file. Only figures written as numerals can be covered; the site
spells out a sentence-opening number and those stay unchecked.

Two numbers in an earlier draft of this file were wrong and are corrected above:
the competition finding is 51 contracts and $45.5M read from documents, not the
~104 and $216M a filename regex suggested, and the modification dedup is on the
filename's free text, not on a parsed modification number.

## What is worth doing next

**Decide about OCR, and say the 51 is a floor until then.** 652 technology
contracts carrying a D&F were never sampled and 63 returned only scans, so 51 is
a floor rather than a rate. The unreadability is not randomly placed: 56% of
sole-source-named D&Fs have no text layer against 7% of cooperative ones, which
means the published figure understates the finding in the direction that
flatters the portal. A trial was run to price OCR per document and measure
whether the text comes back usable; `analysis/ocr_awards.py` holds the pattern
(rasterise pages with ink but no text, OCR via gpt-5.4-mini, cache to
data/pdftext_ocr). Full coverage was estimated at 2,002 more downloads of which
roughly 450 need OCR.

**Rebuild the excerpt for contracts with no lettered sections.** `delivery_excerpt`
shows the model a median 10.4% of the award text, and for 140 of 292 contracts
it finds neither a Section B nor a Section F heading and falls back to the first
9,000 characters -- the cover pages. Those are task orders and GSA-schedule
orders that do not use DC's form. Target the price schedule: that is what the
hand labellers actually used. Two attempted fixes both scored worse and are
recorded in `docs/CLASSIFIER_ACCURACY.md`, so read that before trying a third.

**Widen the gold set before trusting any accuracy number.** The random sample is
29 contracts and its 95% interval runs 37-73%. `tests/hand_labels_delivery.csv`
holds 80 labels with the passage each rests on; the protocol the raters worked from is
`docs/LABELLING_PROTOCOL.md`, so a second round is comparable with the first.

**Reconsider the six delivery labels.** Two careful readers agree 83%. The
scheme has no cell for a one-time build plus the operations of the thing built,
which is common here, and no way to say "half build, half hosting". Collapsing
it -- fixed scope, capacity, neither -- would likely raise both the ceiling and
the classifier.

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
