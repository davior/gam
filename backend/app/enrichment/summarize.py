"""The summarize job: turn what an asset contains into a paragraph about it.

The first job in M6 to produce something a person reads, and the first AI write path in
the app — so it is also the first caller of `apply_ai_metadata`, which is what keeps a
re-run from replacing a summary somebody wrote by hand (FR 8.1.3).

What it reads is not this module's decision; `enrichment/source.py` makes it once for
every job that will need it. For a transcribed video that is the transcript.
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
    "You write summaries for a media library. The person reading yours is trying to "
    "find this file again later, sometimes years afterwards, often remembering only "
    "roughly what was in it.\n\n"
    "Write one paragraph of plain prose. Lead with what the thing actually is, then "
    "what it covers — the specific names, places, claims and terms someone would "
    "search for. Prefer the concrete over the general.\n\n"
    "Never open with a phrase like 'This video' or 'The transcript shows'. Do not "
    "editorialise, do not assess whether anything said is true, and do not add a "
    "preamble, a heading, or anything after the paragraph."
)

# Enough for a real paragraph and not enough for an essay. The model is told to write
# one paragraph; this is the backstop for when it does not listen.
MAX_SUMMARY_CHARS = 2000


def _prompt(asset: Asset, material: source.SourceMaterial) -> str:
    parts = [f"Filename: {asset.original_name or asset.name}"]
    if asset.description:
        # A description the user wrote is context, not something to restate.
        parts.append(f"The owner's own note about it: {asset.description}")

    if material.kind == source.FROM_TRANSCRIPT:
        header = "Transcript of the recording"
        if material.truncated:
            # Said plainly so the model does not write "the talk concludes by…" about
            # material it was never shown.
            header += " (the opening portion only — it continues beyond this)"
        parts.append(f"{header}:\n\n{material.text}")
    elif material.kind == source.FROM_POSTER:
        parts.append(
            "No transcript is available, so a single still frame from the video is "
            "attached. Describe what can be seen and say plainly that it is one frame."
        )
    else:
        parts.append("The image itself is attached.")

    return "\n\n".join(parts)


def run(session: Session, asset: Asset, progress: Progress) -> str:
    """Summarise one asset. Returns a short detail line for the job row."""
    provider = build_provider(session, asset.user_id)
    if provider is None:
        raise ProviderUnavailable(
            "No AI provider is configured. Add one in Settings to enable enrichment."
        )

    progress("Reading the asset", 10, "")
    material = source.gather(session, asset, supports_images=provider.supports_images)

    detail = (
        f"{len(material.text):,} characters of transcript"
        if material.kind == source.FROM_TRANSCRIPT
        else "one frame"
    )
    progress("Summarising", 30, detail)

    completion = provider.complete(
        _prompt(asset, material),
        system=SYSTEM_PROMPT,
        images=material.images,
    )

    # Recorded before this job decides what to do with the answer, so usage does not
    # depend on the outcome: a reply that gets trimmed, or that provenance then refuses
    # to write, cost the same tokens as one that lands.
    #
    # Not a complete guarantee, and the gap is worth knowing: a reply the *provider's*
    # parser rejects — an empty completion, unusable JSON — raises before there is a
    # Completion to record, so those tokens go unaccounted. Closing that means letting
    # ProviderError carry usage, which is a change to the provider contract rather than
    # to this job.
    usage_events.record_completion(session, asset.id, asset.user_id, completion)

    text = completion.text.strip()
    if not text:
        raise ProviderError("The provider returned an empty summary")
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rstrip()

    # Checkpointed before the write, so a cancel pressed during a long completion does
    # not still land a summary afterwards.
    progress("Saving", 90, "")

    written = asset_service.apply_ai_metadata(session, asset, {"summary": text})
    if not written:
        # Not an error. The user wrote their own summary, which outranks this one — but
        # saying so beats a job that reports success and changed nothing.
        return "Left alone — you wrote this summary yourself"

    logger.info(
        "Summarised asset %s from %s (%d in / %d out tokens)",
        asset.id,
        material.kind,
        completion.usage.input_tokens,
        completion.usage.output_tokens,
    )
    return f"Summarised from the {material.kind}"


def summarisable(asset: Asset) -> bool:
    """Whether there is any point offering this.

    Deliberately permissive about *which* material exists — `source.gather` decides
    that, and it needs a provider to know whether images count. This only rules out an
    asset that has no bytes at all.
    """
    return bool(asset.storage_key or asset.thumb_key)
