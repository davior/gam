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

Notes (`proposal["notes"]`) are written into Gecko Notes with the person's own token,
which only this request has (a turn runs in the background as GAM, with none). Their
`/a/<id>` links become absolute here, where GAM's address is known.

Playlists (`proposal["playlists"]`) go through services/playlists, the same as a person
building one. Each item is checked again; one whose asset has gone or whose range no
longer fits is left out and reported, and the rest of the playlist is still made.

Tag changes (`proposal["tag_ops"]`) are to the vocabulary rather than to one asset: new
categories, categories moved, tags filed, renamed, merged and deleted. They run in the
order one depends on another (a category is made before tags are filed into it, and tags
are filed before they are merged away), each checked against the library as it is now, and
one that no longer makes sense is skipped with a sentence while the rest still apply.
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
from app.models.tag import Tag, TagCategory
from app.providers import build_provider
from app.providers.base import ProviderError
from app import notes_client
from app.services import assets as asset_service
from app.services import playlists as playlist_service
from app.services import tags as tag_service
from app.storage import capacity

logger = logging.getLogger(__name__)

KINDS = {"summarize": KIND_SUMMARIZE, "describe": KIND_DESCRIBE, "autotag": KIND_AUTOTAG}


def apply(session: Session, user_id: str, proposal: dict,
          only: Optional[Iterable[str]] = None,
          only_clips: Optional[Iterable[str]] = None,
          only_playlists: Optional[Iterable[str]] = None,
          only_notes: Optional[Iterable[str]] = None,
          only_tag_ops: Optional[Iterable[str]] = None, *,
          token: Optional[str] = None, origin: str = "") -> list[dict]:
    """Apply the proposal's items, clips, playlists and notes, one result per entry.

    `token` is the caller's own, forwarded to Gecko Notes for a note; `origin` is GAM's
    address as the caller reached it, for the links in one.

    No list given: everything. Any given: each restricts its own kind, and a kind left
    out is not applied (a client that names the assets it wants has never heard of clips
    or playlists, and must not get them as a side effect).
    """
    everything = (only is None and only_clips is None and only_playlists is None
                  and only_notes is None and only_tag_ops is None)
    wanted_ops = set(only_tag_ops) if only_tag_ops is not None else set()
    wanted_notes = set(only_notes) if only_notes is not None else set()
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
    for entry in proposal.get("notes", []):
        if everything or entry["note_ref"] in wanted_notes:
            results.append(_apply_note(entry, token, origin))
    ops = [op for op in proposal.get("tag_ops", []) if everything or op["op_ref"] in wanted_ops]
    if ops:
        results.extend(_apply_tag_ops(session, user_id, ops))
    return results


# The order tag changes are applied in, whatever order they were proposed in: a category
# exists before anything is filed under it, and a tag is filed before a merge carries its
# category over to the target.
TAG_OP_ORDER = ("create_category", "move_category", "file_tags", "rename_tag", "merge_tags",
                "delete_tag")


def _apply_tag_ops(session: Session, user_id: str, ops: list[dict]) -> list[dict]:
    """Apply the chosen tag changes, one result each, in the proposal's order.

    `made` maps a proposed category's op_ref to the category it became, so an op naming a
    new parent or home finds it; one whose category was not chosen or could not be made is
    skipped. Every asset whose tag names changed is re-indexed once, at the end, rather than
    once per op that touched it.
    """
    results: dict[str, dict] = {}
    made: dict[str, str] = {}
    reindex: set[str] = set()
    for op in sorted(ops, key=lambda o: TAG_OP_ORDER.index(o["kind"])
                     if o["kind"] in TAG_OP_ORDER else len(TAG_OP_ORDER)):
        result = {"asset_id": None, "asset_name": _tag_op_label(op), "applied": [],
                  "skipped": [], "op_ref": op["op_ref"]}
        results[op["op_ref"]] = result
        try:
            reindex.update(_apply_tag_op(session, user_id, op, made, result))
        except Exception as exc:  # noqa: BLE001 - one bad op must not sink the others
            logger.exception("Applying tag change %s failed", op.get("kind"))
            session.rollback()
            result["skipped"].append(f"It could not be applied: {exc}")
    if reindex:
        asset_service.reindex_ids(session, reindex)
    return [results[op["op_ref"]] for op in ops]


def _tag_op_label(op: dict) -> str:
    kind = op["kind"]
    if kind == "create_category":
        return op["name"]
    if kind == "move_category":
        return op["name"]
    if kind == "file_tags":
        return op.get("category_label") or "No category"
    if kind == "rename_tag":
        return op["from"]
    if kind == "merge_tags":
        return op["target_name"]
    return op.get("name") or ""


def _parent_id(session: Session, user_id: str, op: dict, made: dict[str, str],
               ref_key: str, id_key: str) -> tuple[bool, Optional[str]]:
    """The category an op points at, as (found, id). None with found means "no category"."""
    if op.get(ref_key):
        made_id = made.get(op[ref_key])
        return (made_id is not None, made_id)
    if op.get(id_key):
        row = session.get(TagCategory, op[id_key])
        if row is None or row.user_id != user_id:
            return False, None
        return True, row.id
    return True, None


def _apply_tag_op(session: Session, user_id: str, op: dict, made: dict[str, str],
                  result: dict) -> list[str]:
    """Apply one tag change; return the assets to re-index."""
    kind = op["kind"]
    if kind == "create_category":
        found, parent_id = _parent_id(session, user_id, op, made, "parent_ref", "parent_category_id")
        if not found:
            result["skipped"].append("Its parent category was not made, or no longer exists.")
            return []
        twin = session.exec(select(TagCategory).where(
            TagCategory.user_id == user_id, TagCategory.parent_category_id == parent_id)).all()
        match = next((c for c in twin if c.name.lower() == op["name"].lower()), None)
        if match is not None:
            made[op["op_ref"]] = match.id
            result["applied"].append("it existed already")
            return []
        category = TagCategory(user_id=user_id, name=op["name"], parent_category_id=parent_id)
        session.add(category)
        session.commit()
        made[op["op_ref"]] = category.id
        result["applied"].append("category made")
        return []

    if kind == "move_category":
        category = session.get(TagCategory, op["category_id"])
        if category is None or category.user_id != user_id:
            result["skipped"].append("The category no longer exists.")
            return []
        if op.get("move"):
            found, parent_id = _parent_id(session, user_id, op, made, "parent_ref",
                                          "parent_category_id")
            if not found:
                result["skipped"].append("Its new parent was not made, or no longer exists.")
                return []
            if tag_service.would_create_cycle(session, user_id, category.id, parent_id):
                result["skipped"].append("A category cannot be moved inside itself.")
                return []
            category.parent_category_id = parent_id
            result["applied"].append("moved")
        if op.get("new_name"):
            category.name = op["new_name"]
            result["applied"].append("renamed")
        session.add(category)
        session.commit()
        return []

    if kind == "file_tags":
        found, category_id = _parent_id(session, user_id, op, made, "category_ref", "category_id")
        if not found:
            result["skipped"].append("The category was not made, or no longer exists.")
            return []
        filed = gone = 0
        for entry in op["tags"]:
            tag = session.get(Tag, entry["id"])
            if tag is None or tag.user_id != user_id:
                gone += 1
                continue
            tag.category_id = category_id
            session.add(tag)
            filed += 1
        session.commit()
        if filed:
            result["applied"].append(f"{filed} tag(s) filed")
        if gone:
            result["skipped"].append(f"{gone} tag(s) no longer exist.")
        return []

    if kind == "rename_tag":
        tag = session.get(Tag, op["tag_id"])
        if tag is None or tag.user_id != user_id:
            result["skipped"].append("The tag no longer exists.")
            return []
        try:
            changed = tag_service.rename_tag(session, tag, op["to"])
        except ValueError as exc:
            session.rollback()
            result["skipped"].append(f"{exc}; merge them instead.")
            return []
        session.commit()
        result["applied"].append("renamed")
        return tag_service.asset_ids_for_tag(session, tag.id) if changed else []

    if kind == "merge_tags":
        sources = [t for t in (session.get(Tag, s["id"]) for s in op["sources"])
                   if t is not None and t.user_id == user_id]
        if op.get("target_id"):
            target = session.get(Tag, op["target_id"])
            if target is None or target.user_id != user_id:
                result["skipped"].append("The tag to merge into no longer exists.")
                return []
        else:
            target = tag_service.get_or_create(session, user_id, op["target_name"])
        sources = [t for t in sources if t.id != target.id]
        if not sources:
            result["skipped"].append("The tags to merge no longer exist.")
            return []
        affected = tag_service.merge_tags(session, sources, target)
        result["applied"].append(f"{len(sources)} tag(s) merged into {target.name}")
        if len(sources) < len(op["sources"]):
            result["skipped"].append(f"{len(op['sources']) - len(sources)} tag(s) no longer exist.")
        return affected

    if kind == "delete_tag":
        tag = session.get(Tag, op["tag_id"])
        if tag is None or tag.user_id != user_id:
            result["skipped"].append("The tag no longer exists.")
            return []
        affected = tag_service.delete_tag(session, tag)
        result["applied"].append("deleted")
        return affected

    result["skipped"].append(f"{kind}: not something the library can do.")
    return []


def _apply_note(entry: dict, token: Optional[str], origin: str) -> dict:
    result = {"asset_id": None, "asset_name": entry.get("title") or "", "applied": [],
              "skipped": [], "note_ref": entry["note_ref"], "note_url": None}
    markdown = entry.get("markdown") or ""
    if origin:
        # The model wrote GAM's own page for an asset, `/a/<id>`; in Notes that is a link
        # to Notes. Linked ids were checked when proposed, and an asset deleted since is
        # left as a link that GAM answers with "not found", which is the truth.
        markdown = markdown.replace("](/a/", f"]({origin.rstrip('/')}/a/")
    try:
        note = notes_client.create_note(token, title=entry.get("title") or "",
                                        markdown=markdown, folder_name=entry.get("folder"))
    except notes_client.NotesError as exc:
        result["skipped"].append(str(exc))
        return result
    result["applied"].append("note created")
    result["note_url"] = note.url
    return result


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
