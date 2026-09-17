"""The generate-all job: summarise, describe and tag one asset in a single press.

Three separate provider calls, run one after another — not the merged, single-prompt
version `docs/m6-ai-enrichment.md` sketches as a future cost optimisation ("one pass,
not three"). That would mean redesigning all three prompts around one shared
completion; this is the "press each button in turn" version turned into one job, which
is what a single "Generate all" control needs and costs nothing extra to get right.

Order is summarize, describe, autotag — not the order the buttons sit in the UI.
`describe`'s own prompt already knows to skip repeating an existing summary ("A summary
already exists; do not repeat it"), so running summarize first is what lets that check
actually have something to check against. `autotag` reads both fields as context, so it
goes last regardless.
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlmodel import Session

from app.enrichment import autotag, describe, summarize
from app.models.asset import Asset

logger = logging.getLogger(__name__)

Progress = Callable[..., None]

_STEPS = (
    ("Summarising", summarize.run),
    ("Describing", describe.run),
    ("Suggesting tags", autotag.run),
)


def run(session: Session, asset: Asset, progress: Progress) -> str:
    """Run summarize, describe and autotag over one asset, in turn.

    Stops at whichever step first raises — the same "one clear error" behaviour a
    single-action job already has, rather than swallowing a failure to force the
    remaining steps to run against material that step already showed is unusable.
    """
    results = []
    total = len(_STEPS)
    for index, (stage, step) in enumerate(_STEPS):
        base = int(index * 100 / total)

        def inner(_stage: str, pct: int, detail: str = "", *, base=base) -> None:
            progress(stage, base + pct // total, detail)

        progress(stage, base, "")
        results.append(step(session, asset, inner))

    return " · ".join(results)
