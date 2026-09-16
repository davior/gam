"""The describe job: say what is *in* an asset, in words someone would search for.

The distinction from `summarize` is the whole reason both exist, and it is easy to
collapse: a description says what the thing contains — who is on screen, what the
picture shows, the names and terms a person would type — while a summary says what it
is about. Written as one paragraph each, from the same material, they would be the same
paragraph twice.

Two things keep them apart. The prompts ask different questions, and `describe` asks
`source.gather` for the video's still frame *alongside* the transcript, because the
frame is the only source for anything visual and a transcript alone cannot answer "what
does this look like".

`Asset.description` is the field the library's own placeholder already describes:
"What is in this? Anything you write here is searchable."
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlmodel import Session

from app.enrichment import source
from app.models.asset import Asset
from app.providers import build_provider
from app.providers.base import ProviderError, ProviderUnavailable
from app.services import assets as asset_service
from app.usage import events as usage_events

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

SYSTEM_PROMPT = (
    "You write descriptions for a media library. Someone will search this library "
    "later with half-remembered words, and your description is what has to match.\n\n"
    "Write one paragraph saying what is actually in this item: who appears or speaks, "
    "what is shown, the places, organisations, events and specific terms a person would "
    "type. Name things explicitly rather than referring to them generally — 'a man in a "
    "lab coat' helps nobody find anything; a name does.\n\n"
    "Describe, do not judge. Do not assess whether anything shown or said is true, and "
    "do not summarise the argument — another field does that. Never open with 'This "
    "image' or 'The video shows'. No heading, no preamble, nothing after the paragraph."
)

# A description is a retrieval aid, not an essay. The model is asked for one paragraph;
# this is the backstop for when it does not listen.
MAX_DESCRIPTION_CHARS = 2000


def _prompt(asset: Asset, material: source.SourceMaterial) -> str:
    parts = [f"Filename: {asset.original_name or asset.name}"]
    if asset.summary:
        # Context, not something to restate — saying so is what stops the model
        # paraphrasing the summary back as a description.
        parts.append(
            f"A summary already exists; do not repeat it, describe what is in the "
            f"item instead: {asset.summary}"
        )

    if material.kind == source.FROM_TRANSCRIPT:
        header = "Transcript of the recording"
        if material.truncated:
            header += " (the opening portion only — it continues beyond this)"
        parts.append(f"{header}:\n\n{material.text}")
        if material.images:
            parts.append(
                "A still frame from the recording is also attached. Use it for anything "
                "visual — who is on screen, the setting — which the transcript cannot "
                "tell you."
            )
    elif material.kind == source.FROM_DOCUMENT:
        header = "Text of the document"
        if material.truncated:
            header += " (the opening portion only — it continues beyond this)"
        parts.append(f"{header}:\n\n{material.text}")
    elif material.kind == source.FROM_POSTER:
        parts.append(
            "There is no transcript, so a single still frame from the video is "
            "attached. Describe what can be seen and say plainly that it is one frame."
        )
    else:
        parts.append("The image itself is attached.")

    return "\n\n".join(parts)


def run(session: Session, asset: Asset, progress: Progress) -> str:
    """Describe one asset. Returns a short detail line for the job row."""
    provider = build_provider(session, asset.user_id)
    if provider is None:
        raise ProviderUnavailable(
            "No AI provider is configured. Add one in Settings to enable enrichment."
        )

    progress("Reading the asset", 10, "")
    material = source.gather(
        session,
        asset,
        supports_images=provider.supports_images,
        include_poster=True,
    )

    progress("Describing", 30, "")
    completion = provider.complete(
        _prompt(asset, material),
        system=SYSTEM_PROMPT,
        images=material.images,
    )

    # Recorded before this job decides what to do with the answer, so usage does not
    # depend on the outcome. See summarize.py for the one gap this does not cover.
    usage_events.record_completion(session, asset.id, asset.user_id, completion)

    text = completion.text.strip()
    if not text:
        raise ProviderError("The provider returned an empty description")
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rstrip()

    # Checkpointed before the write: a cancel during a long completion should not still
    # land a description afterwards.
    progress("Saving", 90, "")

    written = asset_service.apply_ai_metadata(session, asset, {"description": text})
    if not written:
        return "Left alone — you wrote this description yourself"

    saw = " and a frame" if material.images and material.kind == source.FROM_TRANSCRIPT else ""
    logger.info(
        "Described asset %s from %s (%d in / %d out tokens)",
        asset.id,
        material.kind,
        completion.usage.input_tokens,
        completion.usage.output_tokens,
    )
    return f"Described from the {material.kind}{saw}"


def describable(asset: Asset) -> bool:
    """Whether there is any point offering this. `source.gather` decides the rest."""
    return bool(asset.storage_key or asset.thumb_key)
