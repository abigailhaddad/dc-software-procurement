"""Classify contracts by what the work actually is, using an LLM.

NIGP commodity codes cannot answer the questions this project cares about. They
are self-assigned, inconsistent between a contract and the purchase orders
against it, and the District's largest IT staffing vehicles are filed under
"miscellaneous services, not otherwise classified". Class 918 mixes IT
consulting with Medicaid managed care; class 920 contains a plumbing
contractor. No threshold rescues this -- see
:func:`dcprocure.analysis.identification_bounds`.

Classification therefore runs on the contract title and vendor, which do
describe the work. The schema and the prompt both come from
``classifier_config.yaml`` so there is one place to change either, following the
same ``fields:`` convention as the generic-comment-analyzer project.

Results are cached on disk keyed by model, config, sample count and input, so
re-runs cost nothing and editing the config invalidates stale labels rather than
silently reusing them.

The cache is doing more work than saving money. The model is not reproducible:
asked the same question twice about the same bytes it does not always give the
same answer, so a cache miss is a re-roll, not a re-read. That was found the
hard way -- the cache was invalidated by accident, 292 award documents were
re-classified, and 31 of 288 changed delivery_structure while the published
totals barely moved because the flips cancelled. Repeat passes since then put
the per-contract rate at roughly 8% for delivery_structure and 13% for
scope_fixed_in_advance, against about 3% for the shorter title-level pass.
Temperature is set to 0 and does reach the provider; it is not a determinism
guarantee and does not act as one here. See :data:`DEFAULT_SAMPLES`.

So every label carries ``field_agreement``, and a published count drawn from
these labels should be quoted with the noise it carries rather than to the unit.
For the delivery pass that noise was measured at a standard deviation of about 2
on a count near 80 with one draw per contract, so +/-4 at 95%; three draws and a
majority narrow it to roughly +/-3.

Requires ``OPENAI_API_KEY`` in the environment or in a ``.env`` at the project
root. Override the model with ``LLM_MODEL`` and the sample count with
``LLM_SAMPLES``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml
from pydantic import BaseModel, Field, create_model

from .pull import DATA_DIR

log = logging.getLogger(__name__)

PROJECT_ROOT = DATA_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "classifier_config.yaml"
CACHE_PATH = DATA_DIR / "classifications.json"

# Matches the model the generic-comment-analyzer defaults to.
DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-5.4-nano")

# How many independent answers to draw per record before taking a majority.
#
# The model is not reproducible on identical input, so one answer per contract
# is one draw from a distribution rather than a reading of the document. Four
# repeat passes over the same 292 award excerpts disagreed with each other on 41
# contracts for delivery_structure and 69 for scope_fixed_in_advance; two
# arbitrary passes differ on about 8% and 13% of contracts respectively. The
# short title-level pass is steadier -- 8 of 250 contracts moved work_type
# across five passes -- but not stable either.
#
# Voting buys less than it looks like it should, because the disagreement is not
# noise around a firm answer: on the contracts that move, the model genuinely
# has no settled view, and a majority of three cannot manufacture one. What it
# does buy is `field_agreement`, which names the contracts whose label is a coin
# flip so a published count can be reported with the noise it carries instead of
# to the unit.
#
# Three is the smallest number that distinguishes unanimous from split. Raising
# it lowers the variance slowly (estimated: 3 -> 5 takes the SD on the
# "sequential and gated" count from about 1.5 to 1.2) and raises the bill
# linearly.
# One draw by default: that is what every published figure was computed from,
# and it keeps the existing cache valid. Majority voting is implemented and
# available with LLM_SAMPLES=3, but its benefit was never measured -- the API
# credits ran out mid-experiment -- and the bootstrap estimate off four
# recorded passes suggested a weak return (5.9% of items differing between
# two runs at k=1, 4.6% at k=3). Turning it on re-classifies everything and
# moves published labels, so it is a deliberate act, not a default.
DEFAULT_SAMPLES = int(os.getenv("LLM_SAMPLES", "1"))

NOT_TECHNOLOGY = "not_technology"
ORDERING_VEHICLE = "ordering_vehicle"


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _field_options(field: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Resolve a field's enum options: inline ``options`` or a shared list."""
    source = field.get("options_from")
    if source:
        entries = config.get(source, [])
        return [e["name"] if isinstance(e, dict) else str(e) for e in entries]
    return list(field.get("options") or [])


def build_result_model(config: dict[str, Any]) -> type[BaseModel]:
    """Build the Pydantic result model from the config's ``fields:`` block.

    Enum-typed fields become real Enums so the provider's structured-output mode
    can only return a listed value.
    """
    model_fields: dict[str, Any] = {}
    for field in config["fields"]:
        name = field["name"]
        description = " ".join((field.get("prompt") or field.get("label") or name).split())
        options = _field_options(field, config)
        if field.get("type") == "single_enum":
            enum = Enum(f"Enum_{name}", {f"V{i}": o for i, o in enumerate(options)}, type=str)
            model_fields[name] = (enum, Field(description=description))
        else:
            model_fields[name] = (str, Field(default="", description=description))
    return create_model("ContractClassification", __base__=BaseModel, **model_fields)


def build_system_prompt(config: dict[str, Any]) -> str:
    """Assemble the system prompt from the config's fields and rules."""
    parts = [
        f"You are performing {config['task_name']}.",
        " ".join(config["task_description"].split()),
        "",
        "For each contract, determine the following fields:",
        "",
    ]
    for i, field in enumerate(config["fields"], 1):
        parts.append(f"{i}. {field.get('label', field['name'])}: "
                     f"{' '.join((field.get('prompt') or '').split())}")
        source = field.get("options_from")
        entries = config.get(source, []) if source else field.get("options") or []
        for entry in entries:
            if isinstance(entry, dict):
                parts.append(f"   - {entry['name']} — {entry['indicator']}")
            else:
                parts.append(f"   - {entry}")
        parts.append("")

    rules = (config.get("classification_rules") or "").strip()
    if rules:
        parts.append("Rules specific to the District of Columbia:")
        parts.append(rules)
    return "\n".join(parts)


CONTRACT_TEMPLATE = """Contract title: {title}
Vendor: {vendor}
Awarding agency: {agency}
Contract value: {amount}
NIGP commodity classes recorded on the contract: {classes}"""

DOCUMENT_TEMPLATE = """Contract title: {title}
Vendor: {vendor}

Excerpt from the contract document:
{excerpt}"""


def _render(record: dict[str, Any]) -> str:
    """Format a record for the prompt, by whichever fields it carries."""
    if record.get("excerpt"):
        return DOCUMENT_TEMPLATE.format(
            title=record.get("title"),
            vendor=record.get("vendor"),
            excerpt=record["excerpt"],
        )
    return CONTRACT_TEMPLATE.format(
        title=record.get("title"),
        vendor=record.get("vendor"),
        agency=record.get("agency"),
        amount=record.get("amount"),
        classes=record.get("classes"),
    )


def is_technology(work_type: str) -> bool:
    """Whether a work type counts as technology.

    Derived from ``work_type`` rather than asked separately: when the model was
    asked both, it sometimes labelled a contract ``not_technology`` while also
    answering that it was technology.
    """
    return work_type != NOT_TECHNOLOGY


def _load_dotenv(root: Path = PROJECT_ROOT) -> None:
    """Read a project-root .env into the environment, without overwriting."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _config_fingerprint(config: dict[str, Any]) -> str:
    """Hash of the parts of the config that change the model's answer."""
    relevant = {k: config[k] for k in config if k != "task_name"}
    return hashlib.sha1(
        json.dumps(relevant, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]


# Fields that decide what a contract buys, and therefore what a change in them
# should force a fresh judgement about. `amount` is deliberately absent: it is
# the sum of every option period's ceiling, so it changes the moment a contract
# exercises an option, even though nothing about what is being bought changed.
# Keying on it meant an option rollover invalidated the entry and the contract
# was re-judged from scratch -- and the model does not always land in the same
# place twice. CW128659, "DOH FY25 Nurse Staffing Temporary Support Services",
# rolled from Base Period to Option 1, its ceiling went $950,000 to $1,900,000,
# and it moved from not_technology to staff_augmentation on identical text.
# With ~4,000 contracts rolling options continuously that churn never settles,
# so published counts drift for reasons that have nothing to do with the
# District. The amount is still shown to the model; it just no longer decides
# whether to ask again.
KEY_FIELDS = ("title", "vendor", "agency", "classes", "excerpt")


def _cache_key(record: dict[str, Any], model: str, fingerprint: str, samples: int) -> str:
    # Only fields actually present take part in the key. Including a field as
    # null when it is absent would change the hash for every existing record the
    # moment a new field is introduced, silently invalidating the whole cache.
    #
    # The sample count is in the key because a one-draw label and a
    # majority-of-three label are not the same measurement and must not be mixed
    # in one published table. Changing DEFAULT_SAMPLES therefore re-classifies
    # everything, at (samples x contracts) calls. Do it deliberately.
    fields = KEY_FIELDS
    payload = json.dumps(
        {k: record[k] for k in fields if record.get(k) is not None},
        sort_keys=True,
        default=str,
    )
    # A single draw is what the historical cache holds, so samples=1 keys the
    # same way it always did. Anything else is a different measurement and gets
    # its own keyspace, which is why raising the count re-classifies the corpus.
    if samples == 1:
        return hashlib.sha1(f"{model}\x00{fingerprint}\x00{payload}".encode()).hexdigest()
    return hashlib.sha1(
        f"{model}\x00{fingerprint}\x00{samples}\x00{payload}".encode()
    ).hexdigest()


def load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")


async def _one_sample(
    user: str,
    model: str,
    system_prompt: str,
    result_model: type[BaseModel],
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> dict[str, Any] | None:
    """One answer from the model, or None once the retries are spent.

    ``temperature=0.0`` is sent and the provider accepts it -- it is not dropped,
    despite ``drop_params`` being on. LiteLLM only drops temperature for a gpt-5
    model when ``reasoning_effort`` is set to something other than "none", and
    this code never sets it (measured: the request body that goes out is
    ``{"model": ..., "temperature": 0.0}``). Temperature 0 is nonetheless not a
    determinism guarantee from OpenAI, and it does not behave as one here: over
    four repeat passes on 292 byte-identical award excerpts, 41 contracts
    returned more than one delivery_structure. Hence the voting below.
    """
    import litellm

    litellm.drop_params = True  # e.g. temperature is unsupported on reasoning models
    litellm.suppress_debug_info = True

    # The semaphore is held per call rather than per record, so raising the
    # sample count does not multiply the number of requests in flight. It does
    # multiply the number of requests, which is what the provider rate-limits
    # against, so a run that used to fit inside the limit may now need a lower
    # `concurrency`.
    async with semaphore:
        for attempt in range(max_retries):
            try:
                response = await litellm.acompletion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Classify this contract:\n\n{user}"},
                    ],
                    response_format=result_model,
                    temperature=0.0,
                    timeout=120,
                )
                # acompletion is typed as returning ModelResponse OR
                # CustomStreamWrapper, and only the latter lacks .choices. The
                # wrapper is returned only for stream=True, which this call never
                # sets, so the union is a stub artefact rather than a case that can
                # occur here; the ignore is on that line alone.
                content = response.choices[0].message.content  # type: ignore[union-attr]
                if content is None:
                    # Not a stub artefact: the provider returns no content on a
                    # refusal, and on a response cut off by the token limit. Both
                    # used to reach json.loads as None and surface as a TypeError
                    # in the retry log, which named neither cause.
                    raise ValueError("model returned an empty response")
                parsed = json.loads(content)
                return result_model(**parsed).model_dump(mode="json")
            except Exception as exc:
                if attempt == max_retries - 1:
                    log.warning("classification call failed: %s", exc)
                    return None
                await asyncio.sleep(2**attempt)
    return None


def _vote(samples: list[dict[str, Any]], enum_fields: Sequence[str]) -> dict[str, Any]:
    """Combine repeat answers into one record, with how close the vote was.

    Each enum field takes its own modal value, because the prompt asks the model
    to judge them independently and the published counts are per-field.

    The free-text fields are NOT voted on -- there is no majority to take over
    quotes and rationales. They are copied from whichever sample agreed with the
    most winning labels, so the ``deliverables_quote`` that is meant to let a
    reader check ``delivery_structure`` belongs to an answer that reached that
    label.

    THE TRAP: at three samples that always holds, because two fields splitting
    2-1 the opposite way still share a sample -- checked over all 292 delivery
    records, none got a combination no single draw produced. At five or more the
    winning combination can belong to no sample, and then the quote argues for a
    label the row does not carry. ``field_agreement`` finds those rows either
    way: anything below 1.0 is a contract the model did not settle, and a count
    built from such rows carries model noise rather than a finding.
    """
    winners = {
        field: Counter(s[field] for s in samples).most_common(1)[0][0]
        for field in enum_fields
    }
    representative = max(samples, key=lambda s: sum(s[f] == winners[f] for f in enum_fields))
    n = len(samples)
    return {
        **representative,
        **winners,
        "field_agreement": {
            field: Counter(s[field] for s in samples)[winners[field]] / n
            for field in enum_fields
        },
    }


def enum_fields(result_model: type[BaseModel]) -> tuple[str, ...]:
    """The fields that carry a label, read off the generated model.

    Taken from the model rather than passed in, so a new ``single_enum`` in a
    config is voted on without anyone remembering to list it here.
    """
    return tuple(
        name
        for name, info in result_model.model_fields.items()
        if isinstance(info.annotation, type) and issubclass(info.annotation, Enum)
    )


async def _classify_one(
    record: dict[str, Any],
    model: str,
    system_prompt: str,
    result_model: type[BaseModel],
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
    samples: int = DEFAULT_SAMPLES,
) -> dict[str, Any] | None:
    """Classify one record, taking the majority of ``samples`` independent draws.

    Returns None only when every draw failed. A partial result is voted on:
    two answers out of three is a worse vote than three, and ``field_agreement``
    says so, which beats discarding a contract over one rate-limited call.
    """
    user = _render(record)
    drawn = await asyncio.gather(
        *(
            _one_sample(user, model, system_prompt, result_model, semaphore, max_retries)
            for _ in range(samples)
        )
    )
    got = [d for d in drawn if d is not None]
    if not got:
        log.warning("classification failed for %r", record.get("title"))
        return None
    return _vote(got, enum_fields(result_model))


async def classify_records(
    records: Sequence[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    concurrency: int = 8,
    config: dict[str, Any] | None = None,
    config_path: Path = CONFIG_PATH,
    checkpoint_every: int = 100,
    samples: int = DEFAULT_SAMPLES,
) -> list[dict[str, Any] | None]:
    """Classify records, reusing cached labels and storing new ones.

    Args:
        records: Dicts with ``title``, ``vendor``, ``agency``, ``amount``,
            ``classes``.
        model: Any LiteLLM model string.
        concurrency: Simultaneous in-flight requests.
        config: Parsed config; loaded from ``config_path`` if omitted.
        config_path: Which YAML config to use. ``classifier_config.yaml``
            classifies contract titles; ``delivery_config.yaml`` reads award PDF
            text for delivery structure.
        checkpoint_every: Write the cache to disk after this many results, so an
            interrupted run keeps its progress.
        samples: Independent answers drawn per record, majority taken. Part of
            the cache key, so changing it re-classifies everything.

    Returns:
        One dict per input record, ``None`` where every draw failed. Each dict
        has the keys declared in the config's ``fields``, plus
        ``field_agreement`` -- the share of draws that backed each label -- and
        ``is_technology``. A ``field_agreement`` entry below 1.0 means the model
        answered that field differently on the same text, so the row is a coin
        flip and any count it feeds is not solid to the unit.
    """
    _load_dotenv()
    config = config or load_config(config_path)
    result_model = build_result_model(config)
    system_prompt = build_system_prompt(config)
    fingerprint = _config_fingerprint(config)

    cache = load_cache()
    keys = [_cache_key(r, model, fingerprint, samples) for r in records]
    todo = [(r, k) for r, k in zip(records, keys) if k not in cache]
    log.info("classify: %d cached, %d to call", len(records) - len(todo), len(todo))

    if todo:
        semaphore = asyncio.Semaphore(concurrency)
        # Checkpoint as results land. A run over a few thousand contracts takes
        # long enough that losing everything to one interruption is a real cost,
        # and the cache is what makes a re-run free.
        for start in range(0, len(todo), checkpoint_every):
            batch = todo[start : start + checkpoint_every]
            results = await asyncio.gather(
                *(
                    _classify_one(r, model, system_prompt, result_model, semaphore,
                                  samples=samples)
                    for r, _ in batch
                )
            )
            for (_, key), result in zip(batch, results):
                if result is not None:
                    cache[key] = result
            save_cache(cache)
            log.info("classified %d/%d", min(start + len(batch), len(todo)), len(todo))

    output: list[dict[str, Any] | None] = []
    for key in keys:
        row = cache.get(key)
        if row is None:
            output.append(None)
            continue
        # Only the title-level config produces work_type; the delivery config
        # has its own fields, so the derived flag is added only where it applies.
        if "work_type" in row:
            row = {**row, "is_technology": is_technology(row["work_type"])}
        output.append(row)
    return output


def classify_contracts(
    contracts: Iterable[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    concurrency: int = 8,
    config_path: Path = CONFIG_PATH,
    samples: int = DEFAULT_SAMPLES,
) -> list[dict[str, Any] | None]:
    """Synchronous wrapper over :func:`classify_records`."""
    return asyncio.run(
        classify_records(
            list(contracts), model, concurrency,
            config_path=config_path, samples=samples,
        )
    )
