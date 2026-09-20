"""Running one enrichment action across a chosen set of assets.

One job for the whole selection, for the reasons `backfill.py` sets out — cancellation
is per row, and forty per-asset jobs mean forty Cancel clicks and forty near-identical
lines in the activity feed — plus one this adds: the consecutive-failure cutoff needs
somewhere to live. A rejected API key fails identically on every asset, and separate
jobs have nowhere to notice that before burning through the selection.

This also finally picks up the `SelectionBar` embed that M5 deferred: `embed` is one of
the actions, so a selection can be made searchable without running the whole library.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sqlmodel import Session

from app.enrichment import attribute, autotag, describe, embed, extract_text, summarize
from app.models.asset import Asset
from app.models.job import (
    KIND_ATTRIBUTE,
    KIND_AUTOTAG,
    KIND_DESCRIBE,
    KIND_EMBED,
    KIND_EXTRACT_TEXT,
    KIND_SUMMARIZE,
)
from app.providers.base import ProviderUnavailable

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

# Same reasoning and same number as `backfill.py`: one pathological asset must not end a
# run, but three in a row indicts the provider rather than the content.
CONSECUTIVE_FAILURE_LIMIT = 3

# What a selection may be put through. Deliberately not "any KIND_": transcription is
# excluded because it is billed per minute of audio and a mis-click over two hundred
# videos is an expensive way to learn that.
ACTIONS = {
    KIND_DESCRIBE: describe.run,
    KIND_SUMMARIZE: summarize.run,
    KIND_AUTOTAG: autotag.run,
    # The action most worth having over a selection after extract_text: twenty
    # screenshots grabbed from one programme in one sitting share every attribution
    # field, and it writes nothing either way — every result is a suggestion.
    KIND_ATTRIBUTE: attribute.run,
    KIND_EMBED: embed.run,
    # Safe to include where transcription is not: it calls nothing and bills nothing, so
    # the mis-click that makes transcription too expensive to offer here costs only time.
    # It is also the action most worth having in bulk — documents arrive by the folder.
    KIND_EXTRACT_TEXT: extract_text.run,
}

# A selection has to be reviewable before it is run, and it is the thing standing between
# a stray Ctrl-A and a four-figure bill.
MAX_SELECTION = 200


@dataclass
class BulkResult:
    action: str
    done: int
    failed: int
    skipped: int


def encode(action: str, asset_ids: list[str]) -> str:
    return json.dumps({"action": action, "asset_ids": asset_ids})


def decode(payload: Optional[str]) -> tuple[str, list[str]]:
    """Read back what a bulk job was asked to do.

    Raises rather than defaulting: a bulk job with no readable payload has no work to do
    and no way to say what it was for, and running "nothing, successfully" would be the
    worst of the available answers.
    """
    try:
        data = json.loads(payload or "")
    except (TypeError, ValueError):
        raise ValueError("This bulk job has no readable payload") from None

    if not isinstance(data, dict):
        raise ValueError("This bulk job has no readable payload")

    action = data.get("action")
    asset_ids = data.get("asset_ids")
    if action not in ACTIONS or not isinstance(asset_ids, list):
        raise ValueError("This bulk job asks for something that cannot be run")

    return action, [a for a in asset_ids if isinstance(a, str)]


def run(session: Session, user_id: str, payload: Optional[str], progress: Progress) -> BulkResult:
    """Apply one action to every asset in the selection that can take it."""
    action, asset_ids = decode(payload)
    runner = ACTIONS[action]
    total = len(asset_ids)

    done = failed = skipped = 0
    consecutive = 0

    for index, asset_id in enumerate(asset_ids):
        asset = session.get(Asset, asset_id)
        if asset is None or asset.user_id != user_id:
            # Deleted between choosing and reaching it, or never theirs. Not a failure —
            # and the ownership check is what stops a crafted selection reaching across
            # accounts, since the ids came from the browser.
            skipped += 1
            continue

        stage = _STAGES[action]
        pct = int(index * 100 / total) if total else 100
        detail = f"{index + 1} of {total} · {asset.name}"

        # Forward the per-asset job's own progress as this job's position rather than
        # dropping it: progress() is the cancellation checkpoint, and one long transcript
        # is minutes of work, so a cancel has to be able to land inside one asset.
        def inner(_stage, _pct, _detail="", *, stage=stage, pct=pct, detail=detail):
            progress(stage, pct, detail)

        progress(stage, pct, detail)

        try:
            runner(session, asset, inner)
            done += 1
            consecutive = 0
        except ProviderUnavailable:
            # The provider went away mid-run. Nothing further will succeed, and failing
            # the job says why once rather than two hundred times.
            raise
        except Exception as exc:  # noqa: BLE001 - one bad asset must not end the run
            failed += 1
            consecutive += 1
            logger.warning(
                "Bulk %s failed on asset %s", action, asset_id, exc_info=True
            )
            if consecutive >= CONSECUTIVE_FAILURE_LIMIT:
                raise RuntimeError(
                    f"Stopped after {consecutive} assets failed in a row. "
                    f"The last error was: {exc}"
                ) from exc

    return BulkResult(action=action, done=done, failed=failed, skipped=skipped)


_STAGES = {
    KIND_DESCRIBE: "Describing",
    KIND_SUMMARIZE: "Summarising",
    KIND_AUTOTAG: "Suggesting tags",
    KIND_EMBED: "Embedding",
    KIND_EXTRACT_TEXT: "Reading text",
}
