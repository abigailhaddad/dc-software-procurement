# Running this weekly

The pipeline refreshes the data and reports what moved. It does not republish
the briefing. That split is the whole design: `analysis/verify_claims.py` holds
the published figures as hardcoded literals and fails when the data no longer
matches them, which is what you want before publishing and useless as a weekly
job. `analysis/drift_report.py` runs the same checks and reports the movement
instead.

## Install

    scripts/install_schedule.sh            # install and load the launchd job
    scripts/install_schedule.sh --remove   # unload and delete it

Mondays at 06:00. If the machine is asleep then, launchd runs the job once when
it next wakes rather than skipping the week. `RunAtLoad` is false, so installing
the job does not immediately start a 300MB download.

Run it now without waiting for Monday:

    launchctl kickstart -p gui/$(id -u)/com.dcbudget.weekly

Or just run the script directly, which is the same thing without launchd:

    scripts/weekly_refresh.sh

Logs go to `data/drift/logs/run-<timestamp>.log`, last 12 runs kept.

## What the weekly run does

0. `make hydrate` -- download any `data/raw` file that is missing from the
   Hugging Face archive. The run stops if this fails: the pull merges into the
   previous files, and starting from nothing would publish one week's rows over
   the archive.
1. `make pull` -- refresh the portal snapshot into `data/raw/`, keeping every
   row the portal has stopped returning (see "The portal forgets" below).
   Then `make publish` uploads it to `archive/` in the dataset.
2. `make classify` -- label anything new.
3. `make rates`, `make solicitations`, `make dcas` -- rebuild the derived tables.
4. `make derived` -- rebuild everything else `verify_claims` reads:
   modifications, competition, trends, vehicles, cycle time, the reseller layer
   and part numbers.
5. `make drift` -- recompute every published figure and report what moved.
6. `make dashboard` -- rebuild `site/dashboard.html`'s data from the new
   snapshot and the drift report.

After a clean run, `make prune` deletes the `data/raw` files to free ~350MB,
and deletes nothing unless every one matches the published copy byte for byte.

Steps 3 and 4 exist because those tables are computed from the snapshot.
Without them the report mixes "the portal changed" with "we never re-ran the
downstream steps", and the output does not distinguish the two. Step 4 was
added after the checks grew to read five directories -- competition,
part_numbers, reseller_layer, trends, vehicle_orders -- that nothing in the
chain rebuilt, so roughly sixty checks would compare a stale table against a
stale literal, pass, and report nothing.

Step 6 is in the chain and `make site` is not. The dashboard reports the current
snapshot, so it should follow every refresh; the briefing stays on the published
baseline until a human restates it.

`make readme` is also outside the chain, for the same reason as `make site`.
README.md is rendered from README.md.in by `analysis/build_readme.py`, and
`verify_claims` fails when re-rendering would change it. That failure is the
signal that the data has moved past what the README says, and restating it is a
decision rather than a step.

Deliberately not in the weekly run:

- `make calc` re-pulls the live GSA CALC API, and its raw responses under
  `data/calc/raw` are the provenance record every federal comparison figure was
  computed from. Re-pulling replaces that record.
- `make h1b` / `make h1b-dol` hit other agencies' systems.
- `make ocr` needs a local OCR toolchain.

Run those by hand when you want them.

## Things that will bite

**A vanished row fails its own claim, not the run.** Twenty-six claims index
a specific row -- contract CW91684's detail record, the FY2021 payment total, a
named labor category on a rate card. If the portal drops one, that claim reports
`<row not found: ...>` and every later check still runs. It is reported
distinctly from a zero on purpose: a contract leaving the portal is a different
event from a number moving, and worth a look.

**One check verifies the whole README.** README.md is generated: `make readme`
renders it from `README.md.in`, computing every figure it states, and the check
is that re-rendering reproduces the committed file byte for byte. Once the data
moves it reads as `True -> False` in the report, which means the README is
stale, not that the check is broken. Re-rendering restates published figures,
so it is a decision someone makes after reading the drift report -- which is why
`make readme` is not in the weekly chain.

**Editing `classifier_config.yaml` invalidates the entire classification cache.**
The cache key is (model, config fingerprint, sample count, record fields), so a
one-word prompt edit re-classifies all 4,013 contracts rather than the handful
that are new. Raising `LLM_SAMPLES` does the same thing, for the same reason: a
one-draw label and a majority-of-three label are different measurements and are
not mixed in one table. The ceiling is deliberately not part of the key -- see
`KEY_FIELDS` in `src/dcprocure/classify.py`.

**The portal forgets.** It is a rolling window: on 1 October 2026, when
FY2027 began, the search stopped returning 1,297 of the 7,165 option periods in
the September pull -- every period of 647 contracts and the oldest periods of
635 still listed. Two published checks failed on it the next week. So the pull
merges rather than replaces: a row the portal drops stays, marked
`listed: False`. Contracts merge on their period id. Payments and purchase
orders merge on groups, because a revised payment comes back changed under the
same id and a revised order under a new `-Vn` number; keeping the old row beside
the new one would count it twice. The rows lost in October were put back with
`dcprocure recover-delisted --from-snapshot <raw/ from the dataset>` and match
the September pull field for field. `src/dcprocure/archive.py` has the detail.

**The payments pull rewrites ~300MB every run.** There is no incremental path:
the module ignores every filter and returns the whole table or nothing. Budget
the disk.

**The published snapshot's row counts will not reproduce.** A re-pull always
returns the current universe. Compare against the manifest's recorded rows
rather than expecting a match.

## Republishing

When the drift report shows movement worth restating, that is a human pass:
update the figures in the notebook, the site and the README, update the asserted
literals in `analysis/verify_claims.py` to match, and run `make verify` until it
passes again. The literals are the record of what is currently published, so they
move last, after the prose they describe.

## The delivery classifier rests on weak evidence

This is a known, measured, unfixed problem. The labels in
`data/delivery_structure.csv` stand, because the one attempt to fix it scored
worse and was reverted.

**The diagnosis.** `delivery_excerpt` gives the model Section B (pricing) and
Section F (deliverables schedule), and never Section C, the scope of work --
Section C is used only as a stop marker. The prompt then says to judge "from
the price schedule and deliverables schedule alone". So the part of the document
that describes how work runs is both absent and forbidden.

What that produces, over the 292 readable awards:

- Of 80 contracts labelled sequential_gated_deliverables, 1 mentions acceptance
  of a deliverable, 2 mention a design phase, 2 mention a requirements phase.
- 39% of them cite the same stock sentence as their evidence: "The Contractor
  shall perform the activities required to successfully complete the District's
  requirements and submit each deliverable". That sentence appears in 38% of all
  award documents, spread evenly across every structure, so it separates nothing.

The published claim "74 of 82 sequential, gated contracts fix scope before work
starts" rests on this. The finding may still be directionally right -- District
contracts probably are mostly fixed-scope -- but the stated evidence does not
establish it.

**What was tried, and what it scored.** Three arms, five runs each, against the
20 hand-labelled cases in `tests/delivery_gold_labels.csv`:

| arm | what it was | correct /20 per run |
|---|---|---|
| published | Section B + F, current prompt | 19, 19, 20, 19, 19 |
| scope of work added | B + C + F, prompt revised to use it | 18, 18, 18, 18, 18 |
| regex + model | as above, plus a mechanical signal block | 16, 16, 16, 16, 16 |

Both attempts are worse, consistently, not within noise. Both were reverted.

Why they failed, as far as one run of 20 cases can say:

- Adding the scope of work makes licence purchases look like services. CW114695
  is goods_or_licenses and moved to recurring_operations once Section C
  described activities around the licences.
- The revised prompt told the model to answer structure_not_stated when only
  standard form is on offer. That over-fired: CW101069 is genuinely
  incremental_capacity and became structure_not_stated every run.
- The regex block listed presence flags from the full document. A mention of
  "licence" anywhere flipped CW101861 and CW102586 from structure_not_stated to
  goods_or_licenses. Presence is not characterisation, and saying so in the
  prompt did not stop it being read that way.

**What is worth trying next**, in rough order:

1. A bigger hand-labelled set. Twenty cases cannot separate a 1-case difference
   from noise, and the current twenty may not contain the failure mode at all --
   the model can score 19/20 while reasoning from a sentence that means nothing.
   Label cases where the stock sentence is the only evidence available.
2. Passage selection inside Section C rather than its first 16,000 characters.
   The prefix misses evidence that sits late in a long scope of work.
3. `reasoning_effort` above "none". The model currently reads a 9,000-character
   legal excerpt and answers 8 fields with zero reasoning tokens.

Only the first makes the other two measurable.

## Baseline

The snapshot the current briefing was computed from is the 2026-08-24 pull. It
cannot be re-fetched -- the portal serves current state only -- so it lives at

    https://huggingface.co/datasets/abigailhaddad/dc-procurement-raw

under `raw_baseline_20260824/`, alongside the raw source data generally. It is
no longer kept on disk. To get it back:

    uv run python -c "from huggingface_hub import snapshot_download; \
      snapshot_download('abigailhaddad/dc-procurement-raw', repo_type='dataset', \
      allow_patterns='raw_baseline_20260824/*', local_dir='data')"

Only raw source material is published there. Everything the model or the OCR
produced -- classifications, extracted rate cards, delivery labels, the drift
reports -- stays local on purpose, so it cannot be mistaken for what the
District published.
