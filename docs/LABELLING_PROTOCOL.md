# Labelling protocol: delivery structure

The brief given to every blind labeller whose work is in
`tests/hand_labels_delivery.csv` and `tests/hand_labels_delivery_rater2.csv`.
Kept so the next round is comparable with this one rather than a fresh
experiment. The `work_type` equivalent is in git history alongside
`tests/hand_labels_work_type.csv`.

# Blind labelling brief: DC contract delivery structure

You are creating GROUND TRUTH for evaluating a classifier. Read the FULL award
document and decide how the work is actually structured.

## Absolute rule: label blind
Do NOT open `data/delivery_structure.csv`, `data/drift/`, or any file containing
the model's existing labels. Do not grep for the contract number in them. If you
see an existing label you have contaminated the case — say so and skip it.
You may read `delivery_config.yaml` (the rubric) and the award documents.

## How to read a document
```python
import sys, json; sys.path.insert(0, "src")
from dcprocure.documents import document_text
text = document_text(None, "<document_id>")   # cached, no network, no API cost
```
Documents run ~100,000 characters. Read the parts that matter: Section C (scope
of work / specifications), Section B (pricing), Section F (period of performance
and deliverables), and any statement of work or attachment describing tasks.

## The six labels
Apply `delivery_config.yaml`'s `delivery_structures` definitions exactly. Read
them from the file; do not work from memory.

## The trap you are here to resolve
This sentence appears in 38% of DC award documents and is spread evenly across
every structure: "The Contractor shall perform the activities required to
successfully complete the District's requirements and submit each deliverable
in accordance with the schedule." It means nothing. A numbered deliverables
table is also standard DC contract form. Neither, alone, establishes a
sequential gated process. Judge from what the document actually describes.

## For each contract, record
- `contract_number`
- `delivery_structure` — one label
- `evidence_basis` — one of:
  - `explicit_process` — the document describes the sequence/phases/gates outright
  - `deliverables_table_only` — an enumerated (ideally dated) deliverables schedule and nothing more
  - `pricing_structure_only` — inferred from how it is priced (hourly, per licence, annual)
  - `scope_description` — inferred from what the work plainly is, not from stated process
  - `none` — nothing in the document speaks to structure
- `quote` — the exact passage the label rests on, verbatim, max ~300 chars. If
  `evidence_basis` is `none`, write "none".
- `alt_label` — the next most defensible label, or "" if genuinely clear
- `confidence` — high / medium / low
- `flag_for_human` — true if a careful person could reasonably disagree, or the
  document is contradictory/unreadable
- `notes` — one sentence on why, and what made it not the alt_label

## Calibration
Being decisive when the document is vague is the failure we are trying to
measure. If the document does not say, `structure_not_stated` with
`evidence_basis: none` is the correct and useful answer, not a cop-out. Equally,
do not hide behind it when the document is clear.

Expect to flag some. A high flag rate on the hard strata is a finding.

## Output
Write a JSON array to the output path you were given. No prose in the file.

## Addendum: a second boilerplate, found while labelling

DHCF's OCIO template contains a verbatim block reading "PHASE I: DISCOVERY AND
PLANNING ... PHASE V: IMPLEMENTATION", with convincing UAT approval language
("five (5) business days to address testing deficiencies and DHCF will
subsequently have five (5) business days to review and approve all UAT
results"). It appears word-for-word in contracts with no services content at
all -- a threat-intelligence SaaS subscription with a $150k platform bundle and
nothing else, and a data-catalog licence whose platform deliverable is due one
day after award.

So convincing gate language is not sufficient either. Check whether the money
and the schedule back it: does the price schedule pay per stage, do the dates
run in sequence, is there anything being built? If a five-phase plan sits on top
of a pure subscription buy, the phases are template text.
