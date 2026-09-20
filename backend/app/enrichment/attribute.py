"""The attribute job: propose where this came from, and write none of it.

The only enrichment in GAM that may not write directly, and the asymmetry is deliberate.
A description the model gets wrong is a poorer search result. A *citation* the model gets
wrong credits somebody else's work to the wrong outlet, and it does so in a field that
looks finished — a blank publisher prompts a fix, a confidently wrong one does not. So
this produces `Suggestion` rows and stops, the same shape FR 9.1.4 gives autotag.

The grounding rule is the other half of that. The model may only return a field it can
quote the text it read it from — a chyron, a byline, a title page, a watermark — and that
quote is stored on the suggestion and shown in the panel. A field it cannot ground is
omitted rather than guessed, because a plausible invention is exactly the failure this
whole design is arranged against, and a reviewer who cannot see what the model read is
not really reviewing anything.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from sqlmodel import Session

from app.enrichment import source
from app.ingest.embedded_metadata import normalise_partial_date
from app.models.asset import Asset
from app.providers import build_provider
from app.providers.base import ProviderError, ProviderUnavailable
from app.services import suggestions as suggestion_service
from app.usage import events as usage_events

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

# What the model may propose. `credit_line` is excluded because it is composed from these
# — proposing a finished sentence would put wording in front of the user that no
# component field supports. `retrieved_at` is excluded because when *you* fetched
# something is not in the content.
PROPOSABLE = ("creator", "publisher", "source_title", "published_date", "source_url", "license")

SYSTEM_PROMPT = (
    "You identify where a piece of media came from, so it can be credited.\n\n"
    "Reply with JSON only, in exactly this shape:\n"
    '{"fields": [{"field": "publisher", "value": "BBC Two", '
    '"evidence": "the on-screen logo in the corner reads BBC TWO"}]}\n\n'
    "Allowed values for \"field\": creator, publisher, source_title, published_date, "
    "source_url, license.\n\n"
    "  creator — the person who made it: author, photographer, speaker, director.\n"
    "  publisher — the outlet, channel, studio or imprint that released it.\n"
    "  source_title — the programme, film, article or book this is part of.\n"
    "  published_date — when it was first published, as YYYY, YYYY-MM or YYYY-MM-DD. "
    "Give only the precision you can actually support: a year alone is a good answer.\n"
    "  source_url — a web address visibly present in the material.\n"
    "  license — a copyright or licence statement visibly present in the material.\n\n"
    "THE RULE THAT MATTERS: include a field ONLY if you can quote the specific thing in "
    "the material that tells you — a caption, a chyron or lower third, a watermark, a "
    "spoken introduction, a byline, a title page, a copyright notice. Put that quote in "
    "\"evidence\". If you cannot point to something, leave the field out entirely.\n\n"
    "Do not infer from style, subject matter, production values, or what is typical. Do "
    "not guess a plausible outlet. An empty list is a correct and useful answer — a "
    "wrong citation is far worse than a missing one.\n\n"
    'If you can ground nothing, reply exactly {"fields": []}.\n\n'
    "Output the JSON and nothing else: no explanation, no markdown fence."
)


def _prompt(asset: Asset, material: source.SourceMaterial) -> str:
    parts = [f"Filename: {asset.original_name or asset.name}"]
    if asset.description:
        parts.append(f"The owner's own note: {asset.description}")

    # What is already recorded, so the model does not spend its answer restating it —
    # and so it can tell that a blank field is genuinely blank rather than withheld.
    known = {
        name: getattr(asset, name)
        for name in PROPOSABLE
        if isinstance(getattr(asset, name, None), str) and getattr(asset, name).strip()
    }
    if known:
        parts.append(
            "Already recorded, do not propose these again:\n"
            + "\n".join(f"  {name}: {value}" for name, value in known.items())
        )

    if material.kind == source.FROM_TRANSCRIPT:
        header = "Transcript"
        if material.truncated:
            header += " (the opening portion only)"
        parts.append(f"{header}:\n\n{material.text}")
    elif material.kind == source.FROM_DOCUMENT:
        header = "Document text"
        if material.truncated:
            header += " (the opening portion only)"
        parts.append(f"{header}:\n\n{material.text}")
    elif material.kind == source.FROM_POSTER:
        parts.append(
            "No transcript is available; a single frame from the video is attached. "
            "Look for a channel logo, a lower third, a watermark or a caption."
        )
    else:
        parts.append(
            "The image itself is attached. Look for a watermark, a credit line, a "
            "caption or a visible byline."
        )

    return "\n\n".join(parts)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_reply(text: str) -> list[dict]:
    """Pull grounded field proposals out of whatever came back.

    Split from the request so it can be tested against recorded replies, which is where
    the grounding rule is actually enforced: a model that returns a field with no
    evidence has not followed the instruction, and this drops it rather than trusting it.
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

    raw = payload.get("fields")
    if not isinstance(raw, list):
        raise ProviderError("The provider did not return usable JSON")

    found: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        name = entry.get("field")
        value = entry.get("value")
        evidence = entry.get("evidence")

        if name not in PROPOSABLE:
            continue
        if not isinstance(value, str) or not value.strip():
            continue

        # The grounding rule, enforced rather than requested. A model that returns a
        # publisher with no evidence has guessed, whatever it says in the prompt — and
        # an ungrounded citation is the precise failure this job is shaped to avoid.
        if not isinstance(evidence, str) or not evidence.strip():
            logger.info("Dropping ungrounded attribution proposal for %s", name)
            continue

        cleaned_value = value.strip()
        if name == "published_date":
            # The model is asked for a partial ISO date and will sometimes answer
            # "March 2019" or "2019-03-15T00:00:00". Normalised through the same reducer
            # the file harvester uses, and dropped if it cannot be read at all — the
            # column has a format and the API will refuse anything else.
            normalised = normalise_partial_date(cleaned_value)
            if not normalised:
                continue
            cleaned_value = normalised

        found.append(
            {"field": name, "value": cleaned_value, "evidence": evidence.strip()}
        )

    return found


def run(session: Session, asset: Asset, progress: Progress) -> str:
    """Propose attribution for one asset. Returns a detail line for the job row."""
    provider = build_provider(session, asset.user_id)
    if provider is None:
        raise ProviderUnavailable(
            "No AI provider is configured. Add one in Settings to enable enrichment."
        )

    progress("Reading the asset", 10, "")
    material = source.gather(session, asset, supports_images=provider.supports_images)

    progress("Looking for a source", 30, "")
    completion = provider.complete(
        _prompt(asset, material),
        system=SYSTEM_PROMPT,
        images=material.images,
    )

    # Recorded before the reply is parsed, so a run that comes back as unusable JSON is
    # still accounted for — the tokens were spent either way.
    usage_events.record_completion(session, asset.id, asset.user_id, completion)

    proposals = parse_reply(completion.text)

    # Checkpointed before the write, so a cancel during a long completion does not still
    # land suggestions afterwards.
    progress("Saving suggestions", 90, "")

    created = suggestion_service.propose_attribution(
        session, asset, proposals=proposals, model=completion.model
    )

    if not created:
        # A true and useful answer, and the one the grounding rule is meant to produce
        # often: most material does not say where it came from, and saying so is better
        # than filling the panel with plausible inventions.
        return "Nothing it could point to"

    logger.info(
        "Attributed asset %s from %s: %d suggestion(s), %d in / %d out tokens",
        asset.id,
        material.kind,
        created,
        completion.usage.input_tokens,
        completion.usage.output_tokens,
    )
    return f"{created} suggestion{'' if created == 1 else 's'} to review"
