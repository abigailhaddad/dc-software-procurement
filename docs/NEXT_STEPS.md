# Where this stands, and what is left

Written 2026-09-07. It exists so the next person -- including a version of you
with no memory of any of this -- can pick the work up without reconstructing it.
Read `docs/CLASSIFIER_ACCURACY.md` alongside it.

## The state in one paragraph

The pipeline runs weekly on its own, rebuilds every table the checks read, and
reports how far the published figures have drifted without republishing them.
The briefing is `site/index.html` and is current with the data: `make verify`
passes on every published figure. README.md states no figures or dates and
holds no findings -- those live on the site, which is where a reader goes. Two
classifiers have been measured against blind hand labels: the one behind most of
the briefing is good, the one behind its former headline was not, and that claim
has been withdrawn and replaced.

## What is running

`com.dcbudget.weekly` runs `scripts/weekly_refresh.sh` on Mondays at 06:00 --
pull, classify, rebuild the derived tables, rebuild everything else the checks
read, drift, dashboard. It does not touch the briefing. Reports land in
`data/drift/`, logs in `data/drift/logs/`, last 12 kept.
`scripts/install_schedule.sh --remove` stops it. Details in
`docs/SCHEDULING.md`.

`scripts/pre-push` refuses a push while `make verify` fails; install it with
`scripts/install_hooks.sh`, skip it with `git push --no-verify`. It exists
because a commit once went out describing a fix its edit had failed to make.

It needs OpenAI credit for newly-appeared contracts. If the key runs dry the
classify step fails, the run reports it and exits non-zero, and nothing silently
serves stale numbers.

## What is settled

**The delivery-structure findings are not publishable.** 55% agreement with
blind hand labels, ±4 of run-to-run noise, and a modification record that
contradicts the interpretation. "74 of 82 sequential, gated contracts fix scope
before work starts" is being replaced. See `docs/CLASSIFIER_ACCURACY.md`.

**The work_type findings are usable with an error bar.** 95% on the
technology/not binary, 90% on the sub-label, from a 40-contract random sample.
Both binary errors inflated the technology count and none deflated it, so
"1,401 technology contracts" is probably slightly high rather than uncertain in
both directions.

**Portal arithmetic is solid.** Corpus counts, the payments arithmetic, the
ceiling-versus-drawdown windows and the `paymentAmount` versus `voucherAmount`
trap involve no model at all. Nothing has touched them.

**But contract-based totals miss whole agencies.** DC Public Schools and the
Office of the Chief Financial Officer hold no contract in the pull, under any
spelling, across every commodity class, while placing $356.8M of core IT
purchase orders. Any figure counted from contracts describes what the portal
publishes, not what the District buys.

**Findings published this session, all with checks behind them:** competition
justifications the portal's own `marketType` never records; ten-year incumbency
and the software lock-in tenure; the reseller layer and the Dell shift;
purchase orders being linkable to contracts through the payments table;
evaluation lag after bids close.

## How the published figures stay honest

**The README states no figures.** It describes the code, and a check fails if a
figure or a date appears in it. site/index.html is hand-written, and its sentences are
string-checked against figures computed from the data, so a number that drifts
fails rather than sits there. Only numerals can be checked that way; a number
the prose spells out at the start of a sentence cannot be.

**The District's own vocabulary is in `procurement_vocabulary.yaml`**, not
compiled into Python: the five spellings of sole source, the D&F header phrases,
the statute table with what each section means. Parsing mechanics stayed in
code. The file is deliberately outside the classifier cache fingerprint, so
editing it cannot invalidate the 4,109 cached labels.

**Figures corrected during this work, because the pattern matters more than the
numbers:** the competition finding was 51 contracts and $45.5M read from the
documents, not the ~104 and $216M a filename regex suggested; modification
deduplication is on the filename's free text, not a parsed modification number;
"27 modifications change the work" became 22 once Section F term clauses stopped
counting as deliverable slips; DCPS and OCFO order totals depend on which
commodity classes you call IT, and the published pair is the one with a check
behind it. In each case a hand-written number disagreed with a computed one and
the check caught it.

## Questions already answered, so nobody spends a day on them again

**OCR of the scanned justifications: done.** 75 documents, $0.77, about a penny
each. 100% yield. It moved the competition finding from 51 contracts to 75. The
remaining ~336 unread documents are the responsibility and price-reasonableness
tiers, which reconnaissance found analytically empty -- one "D&F for
Responsibility" is, in full, a twelve-page credit report.

**Matching purchase orders to vehicles by vendor name: fails.** A holder's
orders after its vehicle starts are 1.22x the period before, against a placebo
of vendors who never held one running 0.35 to 5.90. Inside the noise. It
measures a relationship that already existed.

**Do solicitations name their eventual winner: no.** 1 of 82 award-verified
pairs, and six name a company that did not win. The same matcher fires on 36% of
contract titles, which are written after award, so the matcher works and the
near-zero is real. The apparent cases are incumbent products -- Dell, BMC,
Accela -- named because DC is renewing maintenance on software it already runs.

**Do resellers charge different prices for the same part: unanswerable here.**
29,854 of 30,240 part-number tokens appear in exactly one contract. Eight
repeats are real products, seven are one reseller renewing its own
subscription. One award prices the same IBM part at $324.77 for 162 units and
$407.06 for 40, same vendor, same day, so quantity alone moves a unit price 25%.

**Competition against price, and competition against cycle time:** both died on
overlap, 11 and 12 contracts respectively.

## What is worth doing next

**Rebuild the excerpt for contracts with no lettered sections.** `delivery_excerpt`
shows the model a median 10.4% of the award text, and for 48% of contracts it
finds neither a Section B nor a Section F heading and falls back to the first
9,000 characters -- the cover pages. Those are task orders and GSA-schedule
orders that do not use DC's form. Target the price schedule: that is what the
hand labellers actually used. Two attempted fixes both scored worse and are
recorded in `docs/CLASSIFIER_ACCURACY.md`, so read that before trying a third.

**Widen the gold set before trusting any accuracy number.** The random sample is
29 contracts and its 95% interval runs 37-73%. `tests/hand_labels_delivery.csv`
holds 80 labels with the passage each rests on; the protocol is
`docs/LABELLING_PROTOCOL.md`, so a second round is comparable with the first.

**Reconsider the six delivery labels.** Two careful readers agree 83%. The
scheme has no cell for a one-time build plus the operations of the thing built,
which is common here, and no way to say "half build, half hosting". Collapsing
it -- fixed scope, capacity, neither -- would likely raise both the ceiling and
the classifier.

**Look outside the portal for the independent agencies.** DCPS and OCFO publish
no contracts here. DC Council approves contracts above a threshold regardless of
agency and publishes committee reports and fiscal impact statements; that record
was never checked and would cover the independent agencies and possibly task
orders at once. Unverified as a source -- a lead, not a finding.

## Traps that have already cost time

- `is_technology` comes back from pandas as a numpy bool. `x is True` is False.
  Use `bool(x)`. This has caused three separate wrong answers.
- `contract_details.json` holds one record per contract *option period*.
  Collapsing it with `{d["contractNumber"]: d ...}` loses award documents filed
  under other periods -- 199 contracts' worth. Use
  `documents.award_document_index()`, and note the portal returns option periods
  newest first, so "the first record" is the latest one, not the base period.
- **Payments joined to contracts do not make a time series.** The contracts
  table is current state; ended contracts are absent. The share of payment
  dollars matching a classified contract climbs 0.5% to 13.6% across the decade,
  so a naive series shows 62x growth that is pure survivorship. Join payments to
  purchase orders on the BASE order number instead -- the order table versions
  revised orders (`PO731165-V2`) and the payments table does not, so joining on
  the number as spelled loses half the rows.
- Payment vendor names are truncated at 30 characters, which defeats the
  suffix-stripping in `vendor_aliases.yaml` and splits one company into two.
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
- OCR for the remaining ~336 unread D&Fs. Priced at about a penny a document,
  but they are the tiers already shown to be analytically empty.
- `data/solicitations/profile.md`, a hand-written note pinned to August that no
  script regenerates. It now says so at the top.

## Data that lives elsewhere

The 2026-08-24 raw snapshot the briefing was computed from cannot be re-fetched
-- the portal serves current state only -- so it is on Hugging Face at
`abigailhaddad/dc-procurement-raw`, public, alongside the September pull and the
source documents. Only raw source material is published there; nothing the model
or the OCR produced. `docs/SCHEDULING.md` has the command to pull it back.

## Open: documents re-filed under new ids

The portal sometimes gives a document a new id when it re-files it -- seven
D&Fs on four listed contracts came back between September and October 2026
with new ids, the same names and byte-identical text. Every text cache here
(`data/pdftext*`) is keyed by document id, so a re-filed document reads as
never opened and its finding quietly drops out: that is how CW123727 lost its
software lock-in citation in the 12 October run. Those seven were re-downloaded
under their new ids, and their text matched the old copies exactly. Nothing yet
catches the next one. A fallback that looks for a cached copy under the same
contract and file name, and confirms it by re-downloading, would.

