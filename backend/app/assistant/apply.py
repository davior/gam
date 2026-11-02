"""Applying a proposal: the only place the assistant's work reaches the library.

Everything goes through the services the REST API uses, so an applied proposal indexes,
stamps provenance and queues jobs exactly as a person doing the same by hand would.

Who gets credited for what: a description or summary is written as the AI's
(`apply_ai_metadata`), like any enrichment. A name goes through `apply_metadata`, which
stamps it as a person's, and that is deliberate: a name is the one field the library
never lets an AI replace on its own (services/assets.py), and here a person read the
proposed name and pressed Apply. Tags carry no provenance.

A proposal is applied against the library as it is now. An asset deleted since the turn is
skipped and reported, not an error: the rest of the proposal still stands.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, Optional

from sqlmodel import Session

from app.enrichment.describe import describable
from app.enrichment.summarize import summarisable
from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import KIND_AUTOTAG, KIND_DESCRIBE, KIND_SUMMARIZE
from app.providers import build_provider
from app.providers.base import ProviderError
from app.services import assets as asset_service
from app.services import tags as tag_service

logger = logging.getLogger(__name__)

KINDS = {"summarize": KIND_SUMMARIZE, "describe": KIND_DESCRIBE, "autotag": KIND_AUTOTAG}


def apply(session: Session, user_id: str, proposal: dict,
          only: Optional[Iterable[str]] = None) -> list[dict]:
    """Apply the proposal's items (or just those for `only`), one result per item."""
    wanted = set(only) if only is not None else None
    results = []
    for item in proposal.get("items", []):
        if wanted is not None and item["asset_id"] not in wanted:
            continue
        results.append(_apply_item(session, user_id, item))
    return results


def _apply_item(session: Session, user_id: str, item: dict) -> dict:
    result = {"asset_id": item["asset_id"], "asset_name": item.get("asset_name", ""),
              "applied": [], "skipped": []}
    asset = session.get(Asset, item["asset_id"])
    if asset is None or asset.user_id != user_id:
        result["skipped"].append("The asset no longer exists.")
        return result

    if item.get("name"):
        asset_service.apply_metadata(session, asset, {"name": item["name"]})
        result["applied"].append("name")
    ai_fields = {f: item[f] for f in ("description", "summary") if item.get(f)}
    if ai_fields:
        asset_service.apply_ai_metadata(session, asset, ai_fields)
        result["applied"].extend(ai_fields)

    changed_tags = False
    for name in item.get("add_tags", []):
        tag = tag_service.get_or_create(session, user_id, name)
        if tag is not None and tag_service.attach(session, asset.id, tag.id):
            changed_tags = True
    for name in item.get("remove_tags", []):
        tag = tag_service.find(session, user_id, name)
        if tag is not None and tag_service.detach(session, asset.id, tag.id):
            changed_tags = True
    if changed_tags:
        # The keyword index carries an asset's tags; without this a new tag finds nothing.
        asset_service.reindex_ids(session, [asset.id])
        result["applied"].append("tags")

    for action in item.get("enrich", []):
        reason = _queue_enrichment(session, user_id, asset, action)
        (result["skipped"] if reason else result["applied"]).append(reason or action)
    return result


def _queue_enrichment(session: Session, user_id: str, asset: Asset, action: str) -> Optional[str]:
    """Queue one pass, or say in a sentence why not."""
    kind = KINDS.get(action)
    if kind is None:
        return f"{action}: not something the library can run."
    ready = describable(asset) if action == "describe" else summarisable(asset)
    if not ready:
        return f"{action}: the asset has no file to read."
    try:
        if build_provider(session, user_id) is None:
            return f"{action}: no AI model is set up for the library."
    except ProviderError as exc:
        return f"{action}: {exc}"
    if enrichment_jobs.active_job(session, asset.id, kind) is not None:
        return f"{action}: already running."
    enrichment_jobs.submit(session, asset, kind)
    return None


def decode(raw: Optional[str]) -> Optional[dict]:
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
