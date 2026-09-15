"""The autotag job: propose tags and a title, and apply neither.

FR 9.1.4 is the whole shape of this module. It produces `Suggestion` rows and stops;
turning one into a real tag happens in `services/suggestions.py::accept`, which is only
ever reached from an endpoint a person pressed.

The title rides along here rather than having a job of its own, for the reason
`docs/m6-ai-enrichment.md` gives: it comes from the same reading of the same material,
and a second pass over a long transcript would cost as much again to answer a question
the model has already effectively answered.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from sqlmodel import Session, col, select

from app.enrichment import source
from app.models.asset import Asset
from app.models.tag import Tag
from app.providers import build_provider
from app.providers.base import ProviderError, ProviderUnavailable
from app.services import suggestions as suggestion_service

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

# How many of the library's existing tags to show the model. Enough to establish the
# vocabulary someone has been using, not so many that they crowd out the material. Most
# valuable on a library that has been curated a while, which is exactly when inventing
# a near-duplicate ("US politics" beside an existing "U.S. politics") is most annoying.
VOCABULARY_SIZE = 60

SYSTEM_PROMPT = (
    "You catalogue a personal media library. You are given one item and you propose how "
    "to file it.\n\n"
    "Reply with JSON only, in exactly this shape:\n"
    '{"title": "...", "tags": ["...", "..."]}\n\n'
    "The title is a short, specific name someone would recognise months later — who or "
    "what it is about, not a description of the file. No file extension, no date unless "
    "the date is the point, no trailing punctuation.\n\n"
    "Tags are the handful of things this item is actually about: people, organisations, "
    "places, events, subjects. Prefer a tag from the existing list when it fits — "
    "matching the vocabulary already in use matters more than precision. Propose at "
    "most eight, fewer when fewer will do. No generic tags like 'video', 'interview' or "
    "'media' unless that genuinely distinguishes this item from the rest of a library "
    "full of them.\n\n"
    "Output the JSON and nothing else: no explanation, no markdown fence."
)


def _vocabulary(session: Session, user_id: str) -> list[str]:
    rows = session.exec(
        select(Tag).where(Tag.user_id == user_id).order_by(col(Tag.name)).limit(VOCABULARY_SIZE)
    ).all()
    return [t.name for t in rows]


def _prompt(session: Session, asset: Asset, material: source.SourceMaterial) -> str:
    parts = [f"Filename: {asset.original_name or asset.name}"]
    if asset.description:
        parts.append(f"The owner's own note: {asset.description}")
    if asset.summary:
        parts.append(f"Existing summary: {asset.summary}")

    vocabulary = _vocabulary(session, asset.user_id)
    if vocabulary:
        parts.append("Tags already used in this library:\n" + ", ".join(vocabulary))

    if material.kind == source.FROM_TRANSCRIPT:
        header = "Transcript"
        if material.truncated:
            header += " (the opening portion only)"
        parts.append(f"{header}:\n\n{material.text}")
    elif material.kind == source.FROM_POSTER:
        parts.append("No transcript is available; a single frame from the video is attached.")
    else:
        parts.append("The image itself is attached.")

    return "\n\n".join(parts)


# A model told "JSON only" will still sometimes wrap it in a fence. Cheaper to allow for
# than to fail a whole run over.
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_reply(text: str) -> tuple[str, list[str]]:
    """Pull a title and tags out of whatever came back.

    Split from the request so it can be tested against recorded replies, including the
    malformed ones — which is where this actually earns its keep.
    """
    cleaned = _FENCE.sub("", text or "").strip()
    if not cleaned:
        raise ProviderError("The provider returned an empty reply")

    try:
        payload: Any = json.loads(cleaned)
    except ValueError:
        # Some models prepend a sentence despite being told not to. Take the outermost
        # object rather than discarding an otherwise usable answer.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ProviderError("The provider did not return usable JSON") from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except ValueError:
            raise ProviderError("The provider did not return usable JSON") from None

    if not isinstance(payload, dict):
        raise ProviderError("The provider did not return usable JSON")

    title = payload.get("title")
    title = title.strip() if isinstance(title, str) else ""

    raw_tags = payload.get("tags")
    tags = (
        [t.strip() for t in raw_tags if isinstance(t, str) and t.strip()]
        if isinstance(raw_tags, list)
        else []
    )

    if not title and not tags:
        raise ProviderError("The provider suggested nothing")

    return title, tags


def run(session: Session, asset: Asset, progress: Progress) -> str:
    """Propose tags and a title for one asset. Returns a detail line for the job row."""
    provider = build_provider(session, asset.user_id)
    if provider is None:
        raise ProviderUnavailable(
            "No AI provider is configured. Add one in Settings to enable enrichment."
        )

    progress("Reading the asset", 10, "")
    material = source.gather(session, asset, supports_images=provider.supports_images)

    progress("Choosing tags", 30, "")
    completion = provider.complete(
        _prompt(session, asset, material),
        system=SYSTEM_PROMPT,
        images=material.images,
    )

    title, tags = parse_reply(completion.text)

    # Checkpointed before the write, so a cancel during a long completion does not still
    # land a pile of suggestions afterwards.
    progress("Saving suggestions", 90, "")

    created = suggestion_service.propose(
        session, asset, tags=tags, title=title, model=completion.model
    )

    if not created:
        # A true and useful answer: everything it thought of is already on the asset, or
        # was already declined. Better than "0 suggestions", which reads as a failure.
        return "Nothing new to suggest"

    logger.info(
        "Autotagged asset %s from %s: %d suggestion(s), %d in / %d out tokens",
        asset.id,
        material.kind,
        created,
        completion.usage.input_tokens,
        completion.usage.output_tokens,
    )
    return f"{created} suggestion{'' if created == 1 else 's'} to review"
