# Running this weekly

The weekly run keeps the archive and the site's tables current. The site states
no figures of its own -- everything on it is a query over the tables -- so there
is nothing to restate when the data moves.

## Install

    scripts/install_schedule.sh            # install and load the launchd job
    scripts/install_schedule.sh --remove   # unload and delete it

Mondays at 06:00. If the machine is asleep then, launchd runs the job once when
it next wakes rather than skipping the week. `RunAtLoad` is false, so installing
the job does not immediately start a pull.

Run it now without waiting for Monday:

    launchctl kickstart -p gui/$(id -u)/com.dcbudget.weekly

Or run the script directly, which is the same thing without launchd:

    scripts/weekly_refresh.sh

Logs go to `data/logs/run-<timestamp>.log`, last 12 runs kept.

## What the weekly run does

1. `make hydrate` -- download any `data/raw` file missing from the Hugging Face
   archive. The run stops if this fails: the pull merges into the previous files,
   and starting from nothing would publish one week's rows over the archive.
2. `make pull` -- refresh every table from the portal, in every commodity class,
   keeping rows the portal has stopped returning.
3. `make verify` -- write the Parquet tables (`make export`) and check them
   against the raw files: counts, totals, a sample of rows field by field, and
   that no published value was lost in parsing.
4. `make publish` -- upload the raw files and the tables to `archive/` in the
   dataset. Skipped if step 2 or 3 failed: the live site reads these tables, and
   a broken export would go straight onto it. `data/raw` stays on disk, so
   nothing is lost and the next run publishes it.
5. `make prune` -- delete `data/raw`, and only if every file matches the
   published copy byte for byte.

The analysis behind the retired briefing -- classification, rate cards,
competition, modifications, trends -- is not in the run and is not published.
Its `make` targets still work by hand.

## Things that will bite

**The portal forgets.** It does not keep every record it has published: on
1 October 2026, when FY2027 began, it stopped returning 1,297 of the 7,165
option periods in the September pull. So the pull merges rather than replaces:
a row the portal drops stays, marked `listed: False`. Contracts merge on their
period id. Payments and purchase orders merge on groups, because a revised
payment comes back changed under the same id and a revised order under a new
`-Vn` number; keeping the old row beside the new one would count it twice. The
rows lost in October were put back with `dcprocure recover-delisted
--from-snapshot <raw/ from the dataset>`. `src/dcprocure/archive.py` has the
detail.

**Document ids change.** The portal sometimes re-files a document under a new
id. The site links each document by its current id, so its links stay good, but
any text cache keyed by id goes stale. `docs/NEXT_STEPS.md` has the open fix.

**The payments pull is one large response.** There is no incremental path: the
module ignores every filter and returns the whole table or nothing.

**Row counts will not reproduce.** A re-pull always returns the current
universe. Compare against the manifest's recorded rows rather than expecting a
match.

## Baseline

The snapshot the retired briefing was computed from is the 2026-08-24 pull. It
cannot be re-fetched -- the portal serves current state only -- so it lives at

    https://huggingface.co/datasets/abigailhaddad/dc-procurement-raw

under `raw_baseline_20260824/`, alongside the raw source data generally. It is
no longer kept on disk. To get it back:

    uv run python -c "from huggingface_hub import snapshot_download; \
      snapshot_download('abigailhaddad/dc-procurement-raw', repo_type='dataset', \
      allow_patterns='raw_baseline_20260824/*', local_dir='data')"

Only raw source material is published there. Everything the model or the OCR
produced -- classifications, extracted rate cards, delivery labels -- stays
local on purpose, so it cannot be mistaken for what the
District published.
