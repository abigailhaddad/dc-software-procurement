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

Results are cached on disk keyed by model, config and input, so re-runs cost
nothing and editing the config invalidates stale labels rather than silently
reusing them.

Requires ``OPENAI_API_KEY`` in the environment or in a ``.env`` at the project
root. Override the model with ``LLM_MODEL``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
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


def _cache_key(record: dict[str, Any], model: str, fingerprint: str) -> str:
    # Only fields actually present take part in the key. Including a field as
    # null when it is absent would change the hash for every existing record the
    # moment a new field is introduced, silently invalidating the whole cache.
    fields = ("title", "vendor", "agency", "amount", "classes", "excerpt")
    payload = json.dumps(
        {k: record[k] for k in fields if record.get(k) is not None},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(f"{model}\x00{fingerprint}\x00{payload}".encode()).hexdigest()


def load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")


async def _classify_one(
    record: dict[str, Any],
    model: str,
    system_prompt: str,
    result_model: type[BaseModel],
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> dict[str, Any] | None:
    import litellm

    litellm.drop_params = True  # e.g. temperature is unsupported on reasoning models
    litellm.suppress_debug_info = True

    user = _render(record)

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
                parsed = json.loads(response.choices[0].message.content)
                return result_model(**parsed).model_dump(mode="json")
            except Exception as exc:
                if attempt == max_retries - 1:
                    log.warning("classification failed for %r: %s", record.get("title"), exc)
                    return None
                await asyncio.sleep(2**attempt)
    return None


async def classify_records(
    records: Sequence[dict[str, Any]],
    model: str = DEFAULT_MODEL,
    concurrency: int = 8,
    config: dict[str, Any] | None = None,
    config_path: Path = CONFIG_PATH,
    checkpoint_every: int = 100,
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

    Returns:
        One dict per input record, ``None`` where the call failed. Each dict has
        the keys declared in the config's ``fields``, plus ``is_technology``.
    """
    _load_dotenv()
    config = config or load_config(config_path)
    result_model = build_result_model(config)
    system_prompt = build_system_prompt(config)
    fingerprint = _config_fingerprint(config)

    cache = load_cache()
    keys = [_cache_key(r, model, fingerprint) for r in records]
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
                    _classify_one(r, model, system_prompt, result_model, semaphore)
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
) -> list[dict[str, Any] | None]:
    """Synchronous wrapper over :func:`classify_records`."""
    return asyncio.run(
        classify_records(
            list(contracts), model, concurrency, config_path=config_path
        )
    )
