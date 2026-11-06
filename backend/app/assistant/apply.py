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

Clips and cuts (`proposal["clips"]`) go through the services the Clip tab uses. A clip is
a live window onto the original (`create_clip`: a row, no file, instant). A cut is the same
job the "extract sub-video" button queues, so it is made by the worker, appears in the
activity feed, and the new video shows up in the library when it finishes.

Playlists (`proposal["playlists"]`) go through services/playlists, the same as a person
building one. Each item is checked again; one whose asset has gone or whose range no
longer fits is left out and reported, and the rest of the playlist is still made.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable, Optional

from sqlmodel import Session, col, select

from app.enrichment.describe import describable
from app.enrichment.summarize import summarisable
from app.jobs import enrichment as enrichment_jobs
from app.jobs.runner import ACTIVE_STATUSES
from app.models.asset import Asset
from app.models.job import KIND_AUTOTAG, KIND_DESCRIBE, KIND_EXTRACT_SUBVIDEO, KIND_SUMMARIZE, EnrichmentJob
from app.providers import build_provider
from app.providers.base import ProviderError
from app.services import assets as asset_service
from app.services import playlists as playlist_service
from app.services import tags as tag_service
from app.storage import capacity

logger = logging.getLogger(__name__)

KINDS = {"summarize": KIND_SUMMARIZE, "describe": KIND_DESCRIBE, "autotag": KIND_AUTOTAG}


def apply(session: Session, user_id: str, proposal: dict,
          only: Optional[Iterable[str]] = None,
          only_clips: Optional[Iterable[str]] = None,
          only_playlists: Optional[Iterable[str]] = None) -> list[dict]:
    """Apply the proposal's items, clips and playlists, one result per entry.

    No list given: everything. Any given: each restricts its own kind, and a kind left
    out is not applied (a client that names the assets it wants has never heard of clips
    or playlists, and must not get them as a side effect).
    """
    everything = only is None and only_clips is None and only_playlists is None
    wanted = set(only) if only is not None else set()
    wanted_clips = set(only_clips) if only_clips is not None else set()
    wanted_playlists = set(only_playlists) if only_playlists is not None else set()
    results = []
    for item in proposal.get("items", []):
        if everything or item["asset_id"] in wanted:
            results.append(_apply_item(session, user_id, item))
    for entry in proposal.get("clips", []):
        if everything or entry["clip_id"] in wanted_clips:
            results.append(_apply_clip(session, user_id, entry))
    for entry in proposal.get("playlists", []):
        if everything or entry["playlist_ref"] in wanted_playlists:
            results.append(_apply_playlist(session, user_id, entry))
    return results


def _apply_playlist(session: Session, user_id: str, entry: dict) -> dict:
    result = {"asset_id": None, "asset_name": entry.get("name") or "",
              "applied": [], "skipped": [], "playlist_ref": entry["playlist_ref"],
              "playlist_id": None}
    target = None
    if entry.get("playlist_id"):
        target = playlist_service.owned(session, user_id, entry["playlist_id"])
        if target is None:
            result["skipped"].append("The playlist no longer exists.")
            return result

    specs = []
    for item in entry.get("items", []):
        label = item.get("label") or item.get("asset_name") or "An item"
        try:
            # Checked one by one, so a single stale item is reported rather than sinking
            # the playlist: the service itself refuses a batch with any bad item in it.
            playlist_service.resolve(session, user_id, item["asset_id"],
                                     item.get("in_point"), item.get("out_point"))
        except playlist_service.PlaylistProblem as exc:
            result["skipped"].append(f"{label}: {exc.message}")
            continue
        specs.append(playlist_service.ItemSpec(
            asset_id=item["asset_id"], in_point=item.get("in_point"),
            out_point=item.get("out_point"), label=item.get("label"), note=item.get("reason")))
    if not specs:
        result["skipped"].append("None of its items could be added.")
        return result
    try:
        if target is None:
            target = playlist_service.create(session, user_id, entry.get("name") or "Playlist",
                                             entry.get("description"), specs)
            result["applied"].append("playlist made")
        else:
            playlist_service.add(session, target, specs)
            result["applied"].append("items added")
    except playlist_service.PlaylistProblem as exc:
        session.rollback()
        result["skipped"].append(exc.message)
        return result
    result["playlist_id"] = target.id
    result["asset_name"] = target.name
    result["applied"].append(f"{len(specs)} item(s)")
    return result


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


def _apply_clip(session: Session, user_id: str, entry: dict) -> dict:
    result = {"asset_id": entry["asset_id"], "asset_name": entry.get("asset_name", ""),
              "applied": [], "skipped": [], "clip_id": entry["clip_id"],
              "created_asset_id": None}
    asset = session.get(Asset, entry["asset_id"])
    if asset is None or asset.user_id != user_id:
        result["skipped"].append("The asset no longer exists.")
        return result
    start, end = float(entry["in_point"]), float(entry["out_point"])
    # Again, although the tool checked: the asset may have been replaced or deleted since.
    problem = asset_service.cut_problem(asset, start, end)
    if problem:
        result["skipped"].append(problem)
        return result

    if entry.get("kind") == "cut":
        reason = _queue_cut(session, asset, start, end, entry.get("name"))
        if reason:
            result["skipped"].append(reason)
        else:
            result["applied"].append("cut queued")
        return result

    try:
        clip = asset_service.create_clip(session, asset, in_point=start, out_point=end,
                                         name=entry.get("name"))
    except ValueError as exc:
        result["skipped"].append(f"{exc}.")
        return result
    result["applied"].append("clip made")
    result["created_asset_id"] = clip.id
    return result


# How close two ranges must be to count as the same cut already waiting in the queue.
SAME_CUT_SECONDS = 0.5


def _queue_cut(session: Session, asset: Asset, start: float, end: float,
               name: Optional[str]) -> Optional[str]:
    """Queue the cut as the "extract sub-video" button does, or say in a sentence why not.

    Not refused for another cut of the same asset being in the queue, as the button is: that
    guard exists so two clicks do not make the same video twice, and a request for several
    different moments of one video is the ordinary use here. The same range already
    waiting is refused for the same reason the button's guard exists.
    """
    if not capacity.has_room():
        return capacity.DISK_FULL_MESSAGE
    waiting = session.exec(
        select(EnrichmentJob).where(
            EnrichmentJob.asset_id == asset.id, EnrichmentJob.kind == KIND_EXTRACT_SUBVIDEO,
            col(EnrichmentJob.status).in_(ACTIVE_STATUSES))).all()
    for job in waiting:
        data = decode(job.payload) or {}
        if (data.get("mode") == "extract"
                and abs(float(data.get("in_point", -1)) - start) < SAME_CUT_SECONDS
                and abs(float(data.get("out_point", -1)) - end) < SAME_CUT_SECONDS):
            return "That cut is already being made."
    payload = json.dumps({"mode": "extract", "in_point": start, "out_point": end, "name": name})
    enrichment_jobs.submit(session, asset, KIND_EXTRACT_SUBVIDEO, payload=payload)
    return None


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
