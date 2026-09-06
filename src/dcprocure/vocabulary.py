"""The District's own procurement vocabulary, read from a file rather than typed.

``procurement_vocabulary.yaml`` holds the words: the five ways the portal
spells "sole source", the header phrases that name a Determination and Findings
subtype, the PPRA sections the documents cite and what each one means. This
module loads that file and hands the pieces to the two scripts that need them
-- ``analysis/competition.py``, which reads the documents, and
``analysis/verify_claims.py``, which checks the published figures against them.

Both used to carry their own copy. They matched sole-source spellings
differently, one on a substring and one on a longer substring, and nothing
would have failed if the two had drifted apart to different answers.

The parsing mechanics stay in ``analysis/competition.py``: the en-dash forms a
citation number appears in, the length caps, the field labels that stop a
header regex before it runs into the next block of the form. Those exist
because a particular PDF broke a particular regex, not because of how DC writes
things, and the YAML says so in its header comment.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

VOCABULARY_PATH = Path(__file__).resolve().parents[2] / "procurement_vocabulary.yaml"


@lru_cache(maxsize=1)
def _vocabulary(path: Path = VOCABULARY_PATH) -> dict[str, Any]:
    """Load the file. Cached; it is small and read on every import below."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _pairs(key: str) -> list[tuple[str, str]]:
    """An ordered subtype table as (subtype, pattern) pairs.

    Order is the file's order and it decides the answer -- first match wins --
    so this stays a list, never a dict keyed by subtype.
    """
    return [(row["subtype"], row["pattern"]) for row in _vocabulary().get(key) or []]


#: marketType values that mean the award went to one vendor, exactly as the
#: portal spells them.
SOLE_SOURCE_MARKET_TYPES: tuple[str, ...] = tuple(
    _vocabulary().get("sole_source_market_types") or []
)

_SOLE_SOURCE_KEYS = {" ".join(v.split()).casefold() for v in SOLE_SOURCE_MARKET_TYPES}


def is_sole_source_market_type(value: str | None) -> bool:
    """Whether a portal ``marketType`` is one of the sole-source spellings.

    Whole-field, case-insensitive, whitespace squeezed. Not a substring match:
    "Sole Source" has to be the value of the field, not a word inside it, so a
    future value like "Not a Sole Source" cannot be counted as one.
    """
    if not value:
        return False
    return " ".join(value.split()).casefold() in _SOLE_SOURCE_KEYS


#: Download-priority patterns, keyed by tier name. Each tier's alternatives are
#: joined in file order; the order the tiers themselves are checked in is in
#: :func:`competition.filename_tier`, not here.
FILENAME_TIER_PATTERNS: dict[str, re.Pattern[str]] = {
    tier: re.compile("|".join(alternatives), re.I)
    for tier, alternatives in (_vocabulary().get("filename_tiers") or {}).items()
}

#: Header phrase -> canonical subtype, in the order they are tried.
SUBTYPE_HEADER_PATTERNS: list[tuple[str, str]] = _pairs("subtype_header_patterns")

#: Body text -> canonical subtype, for documents with no usable header.
SUBTYPE_BODY_FALLBACKS: list[tuple[str, str]] = _pairs("subtype_body_fallbacks")

_MEANING = _vocabulary().get("subtype_meaning") or {}

#: Subtypes recording that the District chose not to compete the award.
LIMITS_COMPETITION: frozenset[str] = frozenset(_MEANING.get("limits_competition") or [])
#: Subtypes recording a purchase off another jurisdiction's competition, or a
#: step outside the DC Supply Schedule / set-aside market. A weaker claim, and
#: never merged into :data:`LIMITS_COMPETITION`.
AVOIDS_DC_MARKET: frozenset[str] = frozenset(_MEANING.get("avoids_dc_market") or [])
#: Subtypes that say nothing about how the award was competed.
NEUTRAL: frozenset[str] = frozenset(_MEANING.get("neutral") or [])

#: PPRA sections whose citation is a single-source claim on its face --
#: 2-354.13(8), maintenance of existing software where the creator of the
#: intellectual property is the only source.
LOCK_IN_CITES: tuple[str, ...] = tuple(
    section
    for section, spec in (_vocabulary().get("ppra_sections") or {}).items()
    if (spec or {}).get("counts_as_vendor_lock_in")
)
