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

1. `make pull` -- refresh the portal snapshot into `data/raw/`.
2. `make classify` -- label anything new.
3. `make rates`, `make solicitations`, `make dcas` -- rebuild the derived tables.
4. `make drift` -- recompute all 198 published figures and report what moved.

Step 3 exists because those tables are computed from the snapshot. Without it
the report mixes "the portal changed" with "we never re-ran the downstream
steps", and the output does not distinguish the two.

Deliberately not in the weekly run:

- `make calc` re-pulls the live GSA CALC API, and its raw responses under
  `data/calc/raw` are the provenance record every federal comparison figure was
  computed from. Re-pulling replaces that record.
- `make h1b` / `make h1b-dol` hit other agencies' systems.
- `make ocr` needs a local OCR toolchain.

Run those by hand when you want them.

## Things that will bite

**A vanished row fails its own claim, not the run.** Twenty-four claims index
a specific row -- contract CW91684's detail record, the FY2021 payment total, a
named labor category on a rate card. If the portal drops one, that claim reports
`<row not found: ...>` and every later check still runs. It is reported
distinctly from a zero on purpose: a contract leaving the portal is a different
event from a number moving, and worth a look.

**Roughly fifteen checks verify the README's own prose.** Once the data moves,
those read as `True -> False` in the report. That is correct and it means the
prose is stale, not that the check is broken.

**Editing `classifier_config.yaml` invalidates the entire classification cache.**
The cache key is (model, config fingerprint, record fields), so a one-word prompt
edit re-classifies all 3,983 contracts rather than the handful that are new.

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

## Baseline

`data/raw_baseline_20260824/` is a frozen copy of the snapshot the current
briefing was computed from. `data/raw/` is gitignored, so before that copy
existed the provenance of every published figure lived in exactly one place on
one disk. Keep it until the briefing is republished from a newer snapshot.
