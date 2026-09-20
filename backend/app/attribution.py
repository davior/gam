"""Whose work an asset is, and how that resolves through a clip to its parent.

`Asset.source` says how a file arrived — uploaded, generated, cut from something else.
This module is about the other question entirely: who made it, who published it, what it
was part of, and what may be done with it. Recording that at ingest costs a form field;
reconstructing it later means opening every file by hand, and for anything gathered from
the open web the answer is frequently gone. The decisions behind the field set, and the
alternatives rejected on the way, are in docs/m10-attribution.md.

Everything here is pure: no session, no I/O. That is what lets the composition and
inheritance rules be tested exhaustively without a database, and it is why the callers
that *do* touch a session (services/assets.py) pass the parent in rather than having this
module go and find one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Protocol

# Every attribution field, in one place, so the harvester, the suggestion path, the
# schemas and the index builder cannot drift apart. Adding a ninth field means adding it
# here and following the type errors.
ATTRIBUTION_FIELDS: tuple[str, ...] = (
    "source_url",
    "creator",
    "publisher",
    "source_title",
    "published_date",
    "retrieved_at",
    "license",
    "credit_line",
)

# The subset that reaches the keyword index — the who and the where.
#
# `published_date` and `retrieved_at` are left out on purpose: both are answered exactly
# by the date-range filters on /api/assets, and a bare year in a free-text index mostly
# collides with titles instead of helping. The migration that builds `asset_fts` carries
# its own literal copy of this list; test_attribution.py asserts they still agree.
ATTRIBUTION_TEXT_FIELDS: tuple[str, ...] = (
    "creator",
    "publisher",
    "source_title",
    "license",
    "credit_line",
    "source_url",
)

# What a composed credit line strings together, in reading order.
_CREDIT_ORDER: tuple[str, ...] = (
    "creator",
    "source_title",
    "publisher",
    "published_date",
    "license",
)

_CREDIT_SEPARATOR = " — "


class HasAttribution(Protocol):
    """Structural type for the parts of `Asset` this module reads.

    A Protocol rather than importing Asset: it keeps this module free of the model layer
    (so the tests can drive it with a two-field stub) and documents exactly which columns
    the attribution rules depend on.
    """

    source_url: Optional[str]
    creator: Optional[str]
    publisher: Optional[str]
    source_title: Optional[str]
    published_date: Optional[str]
    retrieved_at: Optional[datetime]
    license: Optional[str]
    credit_line: Optional[str]


@dataclass(frozen=True)
class ResolvedAttribution:
    """One asset's effective attribution, after inheritance."""

    source_url: Optional[str] = None
    creator: Optional[str] = None
    publisher: Optional[str] = None
    source_title: Optional[str] = None
    published_date: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    license: Optional[str] = None
    credit_line: Optional[str] = None
    # Which of the above came from the parent rather than from the asset itself. The API
    # hands this to the UI so an inherited value can be shown as inherited, rather than
    # looking like something typed on the clip and then quietly diverging from it.
    inherited: tuple[str, ...] = ()

    @property
    def credit(self) -> str:
        """The line to display: the override if there is one, else the composition."""
        if not _is_blank(self.credit_line):
            return str(self.credit_line).strip()
        return compose_credit(self)

    @property
    def is_empty(self) -> bool:
        """True when nothing is recorded at all — what the `unattributed` filter means."""
        return all(_is_blank(getattr(self, name)) for name in ATTRIBUTION_FIELDS)


def _is_blank(value: Any) -> bool:
    """Empty for attribution purposes: None, or a string that is only whitespace.

    A datetime is never blank. Written as one helper because "did the user actually put
    something here" is asked by inheritance, by composition and by the unattributed
    filter, and three subtly different answers would be three subtly different bugs.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def compose_credit(source: Any) -> str:
    """Build a one-line citation from whatever fields are filled in.

    Deliberately mechanical: non-empty parts, in a fixed order, joined by an em dash.
    A cleverer format ("Doe, J. *Panorama* (BBC, 2019)") needs rules for every
    combination of missing fields and gets them wrong for the combination nobody tried.
    `credit_line` exists precisely so a user who wants a specific wording can write it,
    and this never has to guess on their behalf.

    Ignores `credit_line` itself — this is what a credit line is composed *from*. Use
    `ResolvedAttribution.credit` to get the override-or-composition.
    """
    parts = []
    for name in _CREDIT_ORDER:
        value = getattr(source, name, None)
        if not _is_blank(value):
            parts.append(str(value).strip())
    return _CREDIT_SEPARATOR.join(parts)


def resolve(
    asset: Any, parent: Any = None
) -> ResolvedAttribution:
    """An asset's effective attribution, falling back to its parent field by field.

    A clip of a documentary has the documentary's publisher without anyone retyping it,
    and correcting the documentary corrects every clip — resolution happens on read, so
    there is no copy anywhere to go stale. Override is per field, not all or nothing, so
    a clip can carry its own creator for one interviewee while keeping the rest.

    **One level, and that is complete rather than a simplification.**
    `services/assets.py::create_clip` refuses to clip a clip, and `promote_clip` leaves
    `parent_asset_id` pointing at the original, so no chain of length two can exist. Do
    not add a recursive walk for a depth the write paths cannot produce.
    """
    values: dict[str, Any] = {}
    inherited: list[str] = []

    for name in ATTRIBUTION_FIELDS:
        own = getattr(asset, name, None)
        if not _is_blank(own):
            values[name] = own
            continue

        from_parent = getattr(parent, name, None) if parent is not None else None
        if not _is_blank(from_parent):
            values[name] = from_parent
            inherited.append(name)
        else:
            values[name] = None

    return ResolvedAttribution(**values, inherited=tuple(inherited))


def index_text(resolved: Any) -> str:
    """The attribution text that goes into `asset_fts`.

    Takes a resolved attribution (or a bare asset) so a clip is findable by the publisher
    it inherited, not only by the fields typed on the clip itself. Keeping a clip out of
    those results would make "everything from the BBC" quietly incomplete in a way the
    user has no way to notice.
    """
    parts = []
    for name in ATTRIBUTION_TEXT_FIELDS:
        value = getattr(resolved, name, None)
        if not _is_blank(value):
            parts.append(str(value).strip())
    return " ".join(parts)
