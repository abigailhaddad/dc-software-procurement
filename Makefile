# Run these in order on a fresh checkout. Each step is resumable: pulls skip
# what is already on disk, and classification is cached by content, so a repeat
# run costs nothing.

.PHONY: help install pull classify verify test report site readme rates ocr h1b \
        h1b-dol calc solicitations dcas pull-payments publish hydrate prune drift dashboard derived competition reseller part-numbers \
        modifications trends vehicles buyers cycle-time all

help:
	@echo "make install    install dependencies"
	@echo "make pull       fetch the portal snapshot into data/raw (~10 min, ~360MB)"
	@echo "make pull-payments  refresh only the payments table (~300MB, one request)"
	@echo "make publish    upload data/raw to the Hugging Face archive (needs HF_TOKEN)"
	@echo "make classify   sort contracts by what they buy, and read delivery structure"
	@echo "make verify     recompute every published figure and fail on drift"
	@echo "make drift      recompute every published figure and report what moved"
	@echo "make test       check document handling, then score the classifiers"
	@echo "make report     print the headline figures"
	@echo "make readme     recompute every figure the README states and re-render it"
	@echo "make site       rebuild the drill-down data behind site/index.html"
	@echo "make dashboard  rebuild the data behind site/dashboard.html"
	@echo "make rates      re-extract hourly rate cards from the cached award texts"
	@echo "make ocr        OCR scanned award documents and classify them separately"
	@echo "make modifications  count contract modifications and read what they change"
	@echo "make h1b        pull H-1B LCA filings for DC tech vendors, flag DC worksites"
	@echo "make h1b-dol    filter DOL's own LCA disclosure files to the vendor set"
	@echo "make calc       pull GSA CALC rates and rebuild the federal comparison"
	@echo "make solicitations  trace technology contracts back to posted solicitations"
	@echo "make dcas       rebuild the DCAS roster and its attributed payments"
	@echo "make derived    rebuild every other table verify_claims checks"
	@echo "make trends     ten-year payment trends: spending, concentration, incumbency"
	@echo "make vehicles   trace purchase orders back to the technology ordering vehicles"
	@echo "make buyers     who buys technology: contracts, payments and purchase orders"
	@echo "make cycle-time days from a solicitation closing to the award it produced"
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

# The portal drops contracts as they age out; data/raw keeps them, and this puts
# that copy somewhere other than one laptop.
publish:
	uv run dcprocure -v publish

# data/raw only needs to exist during a run: hydrate brings it back from the
# archive, prune deletes it once every file is published byte for byte.
hydrate:
	uv run dcprocure -v hydrate

prune:
	uv run dcprocure -v prune

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
	uv run python tests/test_documents.py
	uv run python tests/eval_labels.py
	uv run python tests/eval_delivery.py

report:
	uv run dcprocure report

# README.md is generated from README.md.in. Edit the template, never the
# rendered file: `make verify` fails if re-rendering would change it. Running
# this after a pull restates the README's published figures, so it is a human
# decision like republishing the briefing, and is not in the weekly chain.
readme:
	uv run python analysis/build_readme.py

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

# Caps itself at 400 downloads and caches their text, so a repeat run is free.
# --check-duplicates redoes the byte-hash test behind the deduplication.
modifications:
	uv run python analysis/modifications.py

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

competition:
	uv run python analysis/competition.py --report

reseller:
	uv run python analysis/reseller_layer.py

part-numbers:
	uv run python analysis/part_number_prices.py

# Every table verify_claims checks that is not rebuilt by an earlier step.
# Without this the weekly run refreshes the portal snapshot and leaves these
# frozen, so their checks compare a stale CSV against a stale literal and pass
# while saying nothing. None of these calls a model; competition re-reads its
# document cache rather than downloading.
derived: modifications competition trends vehicles cycle-time reseller part-numbers

# Reads only the payments and purchase-order tables already on disk, so it
# needs no pull and takes about a minute.
trends:
	uv run python analysis/trends.py

# Joins the purchase-order table to the payments table on PO number, which is
# the only link between an order and the contract it was placed against.
# Reads what is already on disk; no pull.
vehicles:
	uv run python analysis/vehicle_orders.py

# Prints its report and writes no CSV: the three views of the buyer disagree,
# and the point is which one you are reading.
buyers:
	uv run python analysis/technology_buyers.py

# Reads what `make solicitations` wrote, so run that first. There is no
# posting date in the solicitations module, so this measures close-to-award
# and not the whole procurement cycle.
cycle-time:
	uv run python analysis/solicitations_cycle_time.py

all: pull classify verify test
