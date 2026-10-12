# DC procurement data

An archive of the records on `contracts.ocp.dc.gov` -- where DC's Office of
Contracting and Procurement publishes contracts, purchase orders and payments --
and a site for exploring them: https://dc-procurement.pages.dev

The site is a copy of what that website lists, as pulled here, not a complete
record of District contracting or spending. It shows the District's fields as
entered and totals them; it classifies nothing and states no findings. What the
data covers, and what is easy to get wrong in it, is on the site's About page,
which computes it from the data rather than stating it here.

## Run it

```bash
make install
make hydrate     # fetch the archived data into data/raw
make pull        # refresh it from contracts.ocp.dc.gov
make export      # write the tables the site reads
make verify      # check those tables against the raw files
make serve       # serve the site locally, reading the local tables
```

`make help` lists the rest.

## How it works

- **Pulls merge, never replace.** A record the source stops returning is kept
  and marked, so the archive only grows. `src/dcprocure/archive.py`.
- **The archive lives on Hugging Face**, in the public dataset
  `abigailhaddad/dc-procurement-raw`, not in git. `make publish` uploads it,
  `make hydrate` fetches it, and `make prune` deletes the local copy only once
  it matches what was published.
- **The tables keep the source's fields.** Each column is a field as published,
  or parsed from one by a stated rule beside the original text.
  `src/dcprocure/export.py` documents every column.
- **One thing is filled in:** agency names published only as an abbreviation,
  code or variant spelling, from `agency_names.yaml`, where every entry cites a
  source. The published value is kept beside the filled-in one.
- **`make verify` checks the tables against the raw files** through a separate
  code path, and the pre-push hook runs it.
- **The site** (`site/`) is static. It loads the tables from the Hugging Face
  dataset and queries them in the browser with DuckDB, so it needs redeploying
  only when its own code changes.

## Schedule and deploys

`scripts/weekly_refresh.sh` runs hydrate, pull, export, verify, publish and
prune; `.github/workflows/weekly.yml` runs it weekly. `.github/workflows/deploy-site.yml`
deploys `site/` to Cloudflare Pages. `docs/SCHEDULING.md` has the details,
including the secrets each needs and what happens when a step fails.

## Other code

`analysis/` holds exploratory analysis that is not part of the site or the
weekly run; `docs/NEXT_STEPS.md` describes it.
