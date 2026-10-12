# Run these in order on a fresh checkout. Each step is resumable: pulls skip
# what is already on disk, and classification is cached by content, so a repeat
# run costs nothing.

.PHONY: help install pull classify verify test report export serve rates ocr h1b \
        h1b-dol calc solicitations dcas pull-payments publish hydrate prune derived competition reseller part-numbers \
        modifications trends vehicles buyers cycle-time all

help:
	@echo "make install    install dependencies"
	@echo "make pull       fetch the portal snapshot into data/raw (~10 min, ~360MB)"
	@echo "make pull-payments  refresh only the payments table (~300MB, one request)"
	@echo "make publish    upload data/raw to the Hugging Face archive (needs HF_TOKEN)"
	@echo "make classify   sort contracts by what they buy, and read delivery structure"
	@echo "make export     write data/raw as the Parquet tables the site reads"
	@echo "make serve      serve the site locally, reading the local export"
	@echo "make verify     check the export against the raw data, and the README for figures"
	@echo "make test       check document handling, then score the classifiers"
	@echo "make report     print the headline figures"
	@echo "make rates      re-extract hourly rate cards from the cached award texts"
	@echo "make ocr        OCR scanned award documents and classify them separately"
	@echo "make modifications  count contract modifications and read what they change"
	@echo "make h1b        pull H-1B LCA filings for DC tech vendors, flag DC worksites"
	@echo "make h1b-dol    filter DOL's own LCA disclosure files to the vendor set"
	@echo "make calc       pull GSA CALC rates and rebuild the federal comparison"
	@echo "make solicitations  trace technology contracts back to posted solicitations"
	@echo "make dcas       rebuild the DCAS roster and its attributed payments"
	@echo "make derived    rebuild the unpublished analysis tables"
	@echo "make trends     ten-year payment trends: spending, concentration, incumbency"
	@echo "make vehicles   trace purchase orders back to the technology ordering vehicles"
	@echo "make buyers     who buys technology: contracts, payments and purchase orders"
	@echo "make cycle-time days from a solicitation closing to the award it produced"
	@echo "make all        pull, export, verify"

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

# The site's tables. Every figure on the site is a query over these, so
# `verify` checks them against the raw files they come from.
export:
	uv run python -c "from dcprocure.export import export; print(export())"

# The pre-push hook runs this. data/raw has to be on disk: hydrate fetches it.
verify: hydrate export
	uv run --with pytest python -m pytest -q tests/test_export.py tests/test_archive.py

# http://127.0.0.1:8811/site/?data=local reads the local export.
serve:
	uv run python -m http.server 8811 --bind 127.0.0.1

test:
	uv run python tests/test_documents.py
	uv run --with pytest python -m pytest -q tests/test_archive.py
	uv run python tests/eval_labels.py
	uv run python tests/eval_delivery.py

report:
	uv run dcprocure report

rates:
	uv run python analysis/extract_rates.py

ocr:
	uv run python analysis/ocr_awards.py

# --report reads every eligible modification already cached and fetches nothing,
# so the sample does not move between weekly runs. Without it the script draws
# and downloads up to 400. --check-duplicates redoes the byte-hash test behind
# the deduplication.
modifications:
	uv run python analysis/modifications.py --report

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

# The analysis tables behind the retired briefing. Not published, and not in
# the weekly run. None of these calls a model; competition re-reads its
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

all: pull export verify
