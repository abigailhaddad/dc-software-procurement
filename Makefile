# Run these in order on a fresh checkout. Each step is resumable: pulls skip
# what is already on disk, and classification is cached by content, so a repeat
# run costs nothing.

.PHONY: help install pull classify verify test report notebook site rates ocr h1b \
        h1b-dol calc solicitations dcas pull-payments drift dashboard all

help:
	@echo "make install    install dependencies"
	@echo "make pull       fetch the portal snapshot into data/raw (~10 min, ~320MB)"
	@echo "make pull-payments  refresh only the payments table (~300MB, one request)"
	@echo "make classify   sort contracts by what they buy, and read delivery structure"
	@echo "make verify     recompute every published figure and fail on drift"
	@echo "make drift      recompute every published figure and report what moved"
	@echo "make test       score the classifiers against hand-read labels"
	@echo "make report     print the headline figures"
	@echo "make notebook   execute the briefing notebook end to end"
	@echo "make site       rebuild the drill-down data behind site/index.html"
	@echo "make dashboard  rebuild the data behind site/dashboard.html"
	@echo "make rates      re-extract hourly rate cards from the cached award texts"
	@echo "make ocr        OCR scanned award documents and classify them separately"
	@echo "make h1b        pull H-1B LCA filings for DC tech vendors, flag DC worksites"
	@echo "make h1b-dol    filter DOL's own LCA disclosure files to the vendor set"
	@echo "make calc       pull GSA CALC rates and rebuild the federal comparison"
	@echo "make solicitations  trace technology contracts back to posted solicitations"
	@echo "make dcas       rebuild the DCAS roster and its attributed payments"
	@echo "make all        pull, classify, verify, test"

install:
	uv sync

# -v is a top-level option and argparse will not accept it after the
# subcommand, so it goes before `pull`.
pull:
	uv run dcprocure -v pull --all-classes

# The payments table is the one module with no working filter: it returns the
# whole table or nothing. Refreshing it alone avoids re-pulling everything else.
pull-payments:
	uv run dcprocure -v pull-payments

classify:
	uv run python analysis/classify_contracts.py
	uv run python analysis/classify_delivery.py
	uv run python analysis/refresh_vendor_aliases.py

verify:
	uv run python analysis/verify_claims.py

# verify fails on drift, which is what you want before publishing. drift only
# reports it, which is what a weekly run needs; it exits non-zero only when the
# run itself breaks. Reports land in data/drift.
drift:
	uv run python analysis/drift_report.py

test:
	uv run python tests/eval_labels.py
	uv run python tests/eval_delivery.py

report:
	uv run dcprocure report

notebook:
	cd analysis && uv run jupyter nbconvert --to notebook --execute --inplace how_dc_buys_software.ipynb

site:
	uv run python analysis/build_site_data.py

# The dashboard shows the current snapshot, so it is rebuilt weekly. The
# briefing stays on the published baseline until someone restates it, which
# is why `site` is not in the weekly chain and this is.
dashboard:
	uv run python analysis/build_dashboard_data.py

rates:
	uv run python analysis/extract_rates.py

ocr:
	uv run python analysis/ocr_awards.py

h1b:
	uv run python analysis/pull_h1b.py

h1b-dol:
	uv run python analysis/pull_h1b_dol.py

# pull_calc hits the live GSA API; the raw responses under data/calc/raw are
# the provenance record, so re-pulling replaces the snapshot every federal
# figure was computed from.
calc:
	uv run python analysis/pull_calc.py
	uv run python analysis/build_calc_comparison.py

# visibility merges the two traces, so it runs last.
solicitations:
	uv run python analysis/solicitations_profile.py
	uv run python analysis/solicitations_pdfscan.py
	uv run python analysis/solicitations_join.py
	uv run python analysis/solicitations_visibility.py

dcas:
	uv run python analysis/build_dcas.py

all: pull classify verify test
