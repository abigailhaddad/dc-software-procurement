> These classifiers belong to the analysis behind the retired briefing. The site no
> longer publishes anything they produce; this records how they were measured.

# What the classifiers actually get right

Measured 2026-09-06 against blind hand labels. Before this, the only evidence
was 21 hand-labelled cases scoring 20/21 and 20/20, which turned out to measure
much less than it appeared to.

## Headline

| classifier | agreement with blind hand labels | n | 95% CI |
|---|---|---|---|
| `is_technology` | **95%** | 40 | 88-100% |
| `work_type` | **90%** | 40 | 81-99% |
| `delivery_structure` | **55%** | 29 | 37-73% |

Two careful readers labelling the same 30 contracts blind agreed with each other
**83%** of the time. That is the ceiling for `delivery_structure`, not 100%: about
one contract in six has no defensible single answer under the six labels. The
model reaches 64% on the subset where both raters agreed.

## Method

Contracts were sampled at random, then labelled by readers who could not see the
model's output. `work_type` labellers were given exactly the fields the
classifier gets -- title, vendor, agency, amount, NIGP classes, no document --
so the comparison is like for like. `delivery_structure` labellers read the full
award document, which is more than the classifier gets; that gap is deliberate,
since the question is what the right answer is, not what is reachable from an
excerpt.

A second stratified sample deliberately oversampled contested `delivery_structure`
cases and scored 60%, slightly *better* than the random sample's 55%. The hard
cases were not the problem; the whole field is.

Labels are in `tests/hand_labels_delivery.csv` (80 contracts, with the passage
each rests on), `tests/hand_labels_delivery_rater2.csv` (30 second-rater labels)
and `tests/hand_labels_work_type.csv` (40).

The 55% above is the random sample alone, which is the unbiased estimate. Since
the OCR pass, 79 of the 80 hand-labelled contracts carry a model label, and
agreement across all 80 is 56% -- higher because the stratified half deliberately
oversampled contested cases and scored better. The 80th is CW124619, an urban
planning contract the District extended in October 2026; on its new title the
classifier moved it out of technology, so it left the delivery set and counts
here as a miss. It was 57% before that. `verify_claims.py` checks the 56%,
because that is the figure a reader can recompute from the files in the
repository; the README and the site quote it with its denominator attached.

## What this means for published figures

Of the 198 claims `verify_claims.py` checks:

- **29 involve no model at all** -- corpus counts, the payments arithmetic, the
  ceiling-versus-drawdown windows. Wrong only if the portal is wrong.
- **58 rest on `work_type`** at 90-95%. Usable with an error bar. Both
  `is_technology` errors in the sample inflated the technology count and none
  deflated it, so "1,372 technology contracts" is probably a slight over-count
  rather than a coin toss.
- **26 rest on `delivery_structure`** at 55%. Not publishable as they stand.
  This includes "74 of 82 sequential, gated contracts fix scope before work
  starts".
- 43 rest on rate-card extraction, which is gold-checked against a
  deterministic reconstruction of CW91684.

## Why delivery_structure fails

Three causes, all measured.

**The excerpt discards the evidence.** `delivery_excerpt` keeps a median 10.4%
of the award text, and for 140 of 292 contracts (48%) it finds neither a Section
B nor a Section F heading and falls back to the first 9,000 characters -- the
cover pages. Those documents are task orders and GSA-schedule orders that do not
use DC's lettered-section form. "Acceptance" appears in 92% of full awards and
24% of excerpts; "design phase" in 6% and 0.3%.

**The strongest available signal is boilerplate.** "The Contractor shall perform
the activities required to successfully complete the District's requirements and
submit each deliverable" appears in 38% of award documents, spread evenly across
every delivery structure, and was cited as the evidence for 39% of the contracts
called sequential and gated. A second, more convincing trap -- DHCF's verbatim
"PHASE I: DISCOVERY AND PLANNING ... PHASE V: IMPLEMENTATION" block with UAT
approval language -- appears in 5 documents including pure subscription buys.

**The question is partly unanswerable.** Across 80 hand-labelled contracts, only
31% could be labelled from an explicit process description; 31% were read off the
price schedule alone and 31% off what the work plainly is. 42% were flagged as
genuinely arguable. The scheme has no cell for a one-time build plus the
operations of the thing built, which is a common shape here.

## Two attempted fixes, both worse

Five runs each over the 20-case gold set:

| variant | correct /20 per run |
|---|---|
| published (Section B + F excerpt) | 19, 19, 20, 19, 19 |
| plus the scope of work, prompt revised | 18, 18, 18, 18, 18 |
| plus a regex signal block | 16, 16, 16, 16, 16 |

Adding the scope of work makes licence purchases look like services. The regex
block listed presence flags from the whole document, and a mention of "licence"
anywhere flipped two contracts to `goods_or_licenses`; saying "presence is not
characterisation" in the prompt did not stop it being read that way. Both were
reverted.

## Reproducibility, separately from accuracy

Temperature is set to 0 and does reach the API (verified by capturing the request
body); it is not a determinism guarantee. Over four repeat passes on identical
input, `delivery_structure` returned more than one answer for 41 of 292
contracts, and any two runs differ on 7.7%. `work_type` differs on 3.2%. That
puts roughly +/-4 of model noise on a count like "82 sequential and gated".
Majority-of-3 voting is implemented behind `LLM_SAMPLES=3` and defaults off; a
bootstrap off the recorded passes suggests it buys little.

## What would actually help

1. Fix `analysis/classify_delivery.py`'s option-period collapse. It builds
   `{d["contractNumber"]: d for d in ...}` over 7,165 records covering 4,011
   contracts and keeps whichever comes last, losing the award document for 59
   technology contracts that have one filed under a different option period.
2. Rebuild the excerpt for the 48% with no lettered sections, targeting the
   price schedule -- which is what the hand labellers actually used.
3. Narrow the question. Report delivery structure only where the document states
   it, and count the rest as not stated, rather than forcing every contract into
   a six-way choice it cannot support.

## Appendix: the delivery classifier rests on weak evidence

This is a known, measured, unfixed problem. The labels in
`data/delivery_structure.csv` stand, because the one attempt to fix it scored
worse and was reverted.

**The diagnosis.** `delivery_excerpt` gives the model Section B (pricing) and
Section F (deliverables schedule), and never Section C, the scope of work --
Section C is used only as a stop marker. The prompt then says to judge "from
the price schedule and deliverables schedule alone". So the part of the document
that describes how work runs is both absent and forbidden.

What that produces, over the 292 readable awards:

- Of 80 contracts labelled sequential_gated_deliverables, 1 mentions acceptance
  of a deliverable, 2 mention a design phase, 2 mention a requirements phase.
- 39% of them cite the same stock sentence as their evidence: "The Contractor
  shall perform the activities required to successfully complete the District's
  requirements and submit each deliverable". That sentence appears in 38% of all
  award documents, spread evenly across every structure, so it separates nothing.

The published claim "74 of 82 sequential, gated contracts fix scope before work
starts" rests on this. The finding may still be directionally right -- District
contracts probably are mostly fixed-scope -- but the stated evidence does not
establish it.

**What was tried, and what it scored.** Three arms, five runs each, against the
20 hand-labelled cases in `tests/delivery_gold_labels.csv`:

| arm | what it was | correct /20 per run |
|---|---|---|
| published | Section B + F, current prompt | 19, 19, 20, 19, 19 |
| scope of work added | B + C + F, prompt revised to use it | 18, 18, 18, 18, 18 |
| regex + model | as above, plus a mechanical signal block | 16, 16, 16, 16, 16 |

Both attempts are worse, consistently, not within noise. Both were reverted.

Why they failed, as far as one run of 20 cases can say:

- Adding the scope of work makes licence purchases look like services. CW114695
  is goods_or_licenses and moved to recurring_operations once Section C
  described activities around the licences.
- The revised prompt told the model to answer structure_not_stated when only
  standard form is on offer. That over-fired: CW101069 is genuinely
  incremental_capacity and became structure_not_stated every run.
- The regex block listed presence flags from the full document. A mention of
  "licence" anywhere flipped CW101861 and CW102586 from structure_not_stated to
  goods_or_licenses. Presence is not characterisation, and saying so in the
  prompt did not stop it being read that way.

**What is worth trying next**, in rough order:

1. A bigger hand-labelled set. Twenty cases cannot separate a 1-case difference
   from noise, and the current twenty may not contain the failure mode at all --
   the model can score 19/20 while reasoning from a sentence that means nothing.
   Label cases where the stock sentence is the only evidence available.
2. Passage selection inside Section C rather than its first 16,000 characters.
   The prefix misses evidence that sits late in a long scope of work.
3. `reasoning_effort` above "none". The model currently reads a 9,000-character
   legal excerpt and answers 8 fields with zero reasoning tokens.

Only the first makes the other two measurable.
