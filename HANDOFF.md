# DC procurement: operator guide

For whoever looks after this next. You don't need to be a developer to read it,
but you need someone who can fix code when the District's portal changes.

## What it does

Every Monday a GitHub Action copies what the District of Columbia's
[Contracts and Procurement Transparency Portal](https://contracts.ocp.dc.gov)
lists (contracts, purchase orders, payments, solicitations) into an archive on
Hugging Face. The portal forgets records as they age out, so the archive keeps
every row it has ever seen and marks the ones the portal no longer returns.
A static site on Cloudflare Pages lets people explore the tables in their
browser. A second Hugging Face dataset, with cleaned Parquet tables and a
dataset card, is for people who want to use the data. Nobody has to run
anything by hand while it works.

No model is used in the weekly run or on the site. The scripts in `analysis/`
(classification, OCR, rate cards) do call a model, through LiteLLM, and need an
API key of their own. They are exploratory and are not part of the weekly run.

## Where things live

| What | Where |
|---|---|
| Code | This GitHub repo |
| Working archive (portal responses, weekly history) | Hugging Face dataset `abigailhaddad/dc-procurement-raw` |
| Public dataset (Parquet tables and card) | Hugging Face dataset `abigailhaddad/dc-procurement`. **Not created yet**: see "Publishing the public dataset" |
| Site | https://dc-procurement.pages.dev (Cloudflare Pages project `dc-procurement`, built from `site/`) |
| Weekly run | `.github/workflows/weekly.yml`, Mondays 10:00 UTC. Logs: the `weekly-refresh-log-<run id>` artifact on each run |
| Site deploy | `.github/workflows/deploy-site.yml`, on a push to `main` that changes `site/` |

`docs/SCHEDULING.md` has the step-by-step of the weekly run and what bites.

## Accounts, secrets and variables

You need a GitHub account, a Hugging Face account and a Cloudflare account. Set
these in the repo's Settings, Secrets and variables, Actions:

| Name | Kind | Used by | What for |
|---|---|---|---|
| `HF_TOKEN` | secret | weekly run | Write access to both Hugging Face datasets above. Without it nothing is published. |
| `CLOUDFLARE_API_TOKEN` | secret | site deploy | A token that can edit Cloudflare Pages. Not set today: the deploy step says so and skips, and the site is deployed by hand. |
| `CLOUDFLARE_ACCOUNT_ID` | secret | site deploy | The Cloudflare account that owns the Pages project. Not set today. |
| `CLOUDFLARE_PROJECT` | variable | site deploy | The Pages project name. Optional: defaults to `dc-procurement`. |
| `PUBLISH_PUBLIC_DATASET` | variable | weekly run | Set to `true` to publish the public dataset each week. Off until you set it. |

## Moving it to a new owner

1. **Copy the archive first.** The weekly run starts by restoring the archive
   from Hugging Face and stops if it can't. The portal serves only its current
   state, so history it has dropped can't be fetched again. Download
   `abigailhaddad/dc-procurement-raw` in full, including `raw_baseline_20260824/`,
   and upload it to a dataset of the same shape in your own Hugging Face account
   (`docs/SCHEDULING.md` shows a `snapshot_download` call). The old owner should
   keep theirs until yours has run clean once.
2. **Point the code at your names.** `src/dcprocure/config.py` holds the GitHub
   repo, the two Hugging Face dataset ids and the site address. Each can be set
   by an environment variable (`DCPROCURE_GITHUB_REPO`, `DCPROCURE_HF_ARCHIVE`,
   `DCPROCURE_HF_DATASET`, `DCPROCURE_SITE_URL`), or change the defaults.
3. **Change the names that can't read that file.** These are plain text:
   - `site/shared/db.js`: the Hugging Face archive address (two lines).
   - `site/about.html`, `site/how-dc-buys.html`, `site/gaps.html`: links to this
     GitHub repo.
   - `src/dcprocure/publish.py`: the archive's dataset card text, which names the
     site and the GitHub repo.
   - `README.md`, `docs/SCHEDULING.md`, `docs/NEXT_STEPS.md`: addresses and the
     repo name.
   - `.github/workflows/deploy-site.yml`: the Pages project name, unless you set
     the `CLOUDFLARE_PROJECT` variable.
   - `scripts/com.dcbudget.weekly.plist` and `scripts/install_schedule.sh`: the
     laptop fallback, switched off. Ignore unless you use it.
4. **Create the Cloudflare Pages project** and add the two Cloudflare secrets so
   pushes deploy the site. Until then deploy by hand:
   `npx wrangler pages deploy site --project-name <your project>`.
5. **Run the weekly workflow once by hand** (`gh workflow run weekly.yml`) and
   read the log before you trust the schedule.

## Publishing the public dataset

The weekly run can also publish `abigailhaddad/dc-procurement` (or your own
name): six Parquet tables, a dataset card, nothing else. **It has not been
published yet.** To check what would go up, from a checkout with the archive
restored (`make hydrate`, then `make export`):

    make publish-dataset

That builds the files into `data/public_dataset/` and checks them against the
archive, and uploads nothing. When you are happy with the files and the card
there, either run `make publish-dataset-upload` once (needs `HF_TOKEN`), or set
the `PUBLISH_PUBLIC_DATASET` variable to `true` and let Monday's run do it. Each
upload is one commit that also creates the dataset, as a public one, if it
doesn't exist.

Two things to decide first, both in `src/dcprocure/dataset.py`:

- **Which columns are left out** (`DROP_COLUMNS`). The default leaves out the
  hand-written `of_interest` notes on contracts, and the specialists' email and
  phone on solicitations. Names of contracting officers, solicitation specialists
  and vendors are kept, as the portal publishes them. Some payees are individual
  people or sole practitioners. If you would rather not republish those, drop the
  columns here, or the rows.
- **The data's terms.** The portal's own terms of use were not found. The
  data.gov catalog entry for the portal (published by the Office of Contracting
  and Procurement) lists CC BY 4.0, last modified 2022-02-16. The card says that
  and tells readers to check the portal. It does not assert a license of its own.

`scripts/spot_check_dataset.py RAW_DIR BUILT_DIR` is a second check: it reads the
raw JSON and compares a random and edge-case sample of rows, field by field, with
the built tables. Run it before the first upload.

## When it breaks

A failed run turns red on the Actions tab. The log artifact says which step
failed.

- **"restore raw archive" failed.** The run stops on purpose: pulling without
  the archive would publish one week over the history. Check `HF_TOKEN` and that
  the archive dataset exists.
- **A pull failed.** The portal was down or changed. Nothing is published that
  week and the next run catches up, because the pull merges and doesn't replace.
- **"this pull dropped N of the M rows the archive had listed".** A pull came
  back more than half empty, so the run stopped before writing anything. This is
  almost always a truncated response. If the portal really did shrink, rerun with
  `DCPROCURE_ALLOW_SHRINK=1`. The payments table has its own floor, in
  `src/dcprocure/pull.py` (`PAYMENTS_MIN_ROWS`).
- **A check failed** (`make verify`). The tables didn't match the raw files, so
  nothing was published. Run `make verify` locally to see which.
- **The portal changed its pages.** Someone has to fix the code in
  `src/dcprocure/client.py` and `pull.py`.

## Tests

    make verify    # tables against the raw files, plus the archive and dataset tests
    make test      # the same tests, plus the labelling checks

`tests/test_dataset.py` and `tests/test_archive.py` need no network.

## What hasn't been checked

- Nobody has run the weekly workflow since the changes that added the public
  dataset step, the shrink guard and the project variable. They are tested
  locally only.
- The public dataset has not been created, and the upload has never run
  against Hugging Face. It is tested with a stand-in for the Hugging Face API.
- The Cloudflare deploy step has never run: its secrets are not set.
- Copying the archive to a new account has not been tried.
- The portal's terms of use were not found.
