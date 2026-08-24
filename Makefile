# Run these in order on a fresh checkout. Each step is resumable: pulls skip
# what is already on disk, and classification is cached by content, so a repeat
# run costs nothing.

.PHONY: help install pull classify verify test report notebook site rates all

help:
	@echo "make install    install dependencies"
	@echo "make pull       fetch the portal snapshot into data/raw (~10 min, ~320MB)"
	@echo "make classify   sort contracts by what they buy, and read delivery structure"
	@echo "make verify     recompute every published figure and fail on drift"
	@echo "make test       score the classifiers against hand-read labels"
	@echo "make report     print the headline figures"
	@echo "make notebook   execute the briefing notebook end to end"
	@echo "make site       rebuild the drill-down data behind site/index.html"
	@echo "make rates      re-extract hourly rate cards from the cached award texts"
	@echo "make all        pull, classify, verify, test"

install:
	uv sync

pull:
	uv run dcprocure pull --all-classes -v

classify:
	uv run python analysis/classify_contracts.py
	uv run python analysis/classify_delivery.py
	uv run python analysis/refresh_vendor_aliases.py

verify:
	uv run python analysis/verify_claims.py

test:
	uv run python tests/eval_labels.py
	uv run python tests/eval_delivery.py

report:
	uv run dcprocure report

notebook:
	cd analysis && uv run jupyter nbconvert --to notebook --execute --inplace how_dc_buys_software.ipynb

site:
	uv run python analysis/build_site_data.py

rates:
	uv run python analysis/extract_rates.py

all: pull classify verify test
