# DC procurement: handoff

Every Monday a GitHub Action copies what DC's [Contracts and Procurement Transparency Portal](https://contracts.ocp.dc.gov) lists (contracts, purchase orders, payments, solicitations) into a Hugging Face archive. The portal drops old records, so the archive keeps them and marks them delisted.

- Code: [abigailhaddad/dc-software-procurement](https://github.com/abigailhaddad/dc-software-procurement)
- Public dataset: [abigailhaddad/dc-procurement](https://huggingface.co/datasets/abigailhaddad/dc-procurement). The dataset card has the columns and the limits.
- Working archive: [abigailhaddad/dc-procurement-raw](https://huggingface.co/datasets/abigailhaddad/dc-procurement-raw)
- Site: https://dc-procurement.abigailhaddad.com, built from [abigailhaddad/dataset-explorer](https://github.com/abigailhaddad/dataset-explorer) (slug `dc-procurement`). It reads the data from Hugging Face when a page loads. The old hand-built site is in `site/` for reference and at https://dc-procurement-legacy.pages.dev.

## What runs on its own

- `.github/workflows/weekly.yml` (the schedule is at the top). It restores the archive from Hugging Face, pulls the portal, rebuilds and checks the tables, and publishes. The steps are in `scripts/weekly_refresh.sh`. It also publishes the public dataset when the variable `PUBLISH_PUBLIC_DATASET` is `true` (it is).
- The site deploys from the `dataset-explorer` repo, not this one.

## Secrets and variables

- `HF_TOKEN` (secret): write access to both datasets. Nothing publishes without it.
- `PUBLISH_PUBLIC_DATASET` (variable): `true` publishes the public dataset each week.
- The `CLOUDFLARE_*` secrets on this repo are not used by any workflow here.

## If you take it over

- **Copy the archive first.** The weekly run starts by downloading `dc-procurement-raw` and stops if it can't. The portal only shows its current state, so dropped records can't be fetched again. `docs/SCHEDULING.md` has the download call.
- **Owner names** are in `src/dcprocure/config.py` (set by `DCPROCURE_GITHUB_REPO`, `DCPROCURE_HF_ARCHIVE`, `DCPROCURE_HF_DATASET`, `DCPROCURE_SITE_URL`). The rest are plain text: run `grep -rn abigailhaddad .`. The site's configs are `configs/dc-*.json` in `dataset-explorer`.
- **Before publishing the public dataset,** decide which columns to leave out (`DROP_COLUMNS` in `src/dcprocure/dataset.py`). Also know that the portal's terms of use weren't found. Its data.gov listing says CC BY 4.0.
- Run `weekly.yml` once by hand and read the log before you trust the schedule.

## When it breaks

- A red run is on the Actions tab. The log is the `weekly-refresh-log-<run id>` artifact.
- "restore raw archive" failed: the run stops on purpose. Check `HF_TOKEN` and that the archive dataset exists.
- "this pull dropped N of M rows": the shrink guard (`src/dcprocure/archive.py`) stops a pull that delists more than half the archive, and writes nothing. If the portal really shrank, rerun with `DCPROCURE_ALLOW_SHRINK=1`.
- A check failed (`make verify`): nothing is published. Run it locally to see which.
- The portal changed its pages: the fix is in `src/dcprocure/client.py` and `pull.py`.
- To rerun: Actions, Weekly refresh, Run workflow.

## Costs

No model runs in the weekly job. The scripts in `analysis/` call an LLM through LiteLLM only when run by hand.

Not tested: copying the archive to a new account.
