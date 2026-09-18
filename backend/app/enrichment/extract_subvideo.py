"""The extract_subvideo job: a parent's bytes in, a standalone clip file out.

Two modes carried in the job's `payload` (see `models/job.py::KIND_EXTRACT_SUBVIDEO`):
`"extract"` cuts a fresh range straight from a file-owning asset, leaving it untouched;
`"promote"` does the same cut but writes the result back into an *existing* clip row
(one with no `storage_key` of its own) rather than creating a new one — the one-click
"make this reference a real file" path a blocked delete offers. Both go through the
same ffmpeg mechanics in `enrichment/subvideo.py` and the same Asset-row plumbing in
`services/assets.py`, which is where sub-video creation was always meant to land (see
that module's own docstring).

Runs on a worker thread, so it owns its own session and reports progress through the
callback the queue hands it — which is also the cancellation checkpoint.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session

from app.enrichment.subvideo import SubvideoExtractionError, extract_subvideo
from app.ingest import thumbnails
from app.ingest.filetypes import TYPE_AUDIO
from app.ingest.probe import probe
from app.models.asset import Asset
from app.services import assets as asset_service
from app.storage import build_storage, new_key, thumb_key_for

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], None]


def run(
    session: Session, asset: Asset, payload: Optional[str], progress: Progress
) -> tuple[str, Optional[str]]:
    """Run one extraction or promotion. Returns (detail, created_asset_id).

    `created_asset_id` is only set for `"extract"` mode — the frontend has nowhere
    else to learn the new asset's id, since the job's own `asset_id` stays pointed at
    the *source*, not the row that does not exist until this returns. `"promote"`
    updates the clip's own row in place, so the frontend already has that id: it is
    `job.asset_id` throughout, and nothing new needs reporting.
    """
    try:
        data = json.loads(payload or "{}")
    except ValueError:
        data = {}
    mode = data.get("mode")

    if mode == "promote":
        clip = asset
        if clip.storage_key is not None or not clip.parent_asset_id:
            raise SubvideoExtractionError("This asset is not a clip")
        source_asset = session.get(Asset, clip.parent_asset_id)
        if source_asset is None or not source_asset.storage_key:
            raise SubvideoExtractionError("The original asset is no longer available")
        in_point = clip.in_point or 0.0
        out_point = clip.out_point or 0.0
    else:
        source_asset = asset
        if not source_asset.storage_key:
            raise SubvideoExtractionError("The source file is not available")
        try:
            in_point = float(data["in_point"])
            out_point = float(data["out_point"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SubvideoExtractionError("Missing or invalid in/out points") from exc

    storage = build_storage()
    progress("Extracting", 10, "")

    # Always .mp4 (.m4a for an audio source): both ffmpeg strategies in subvideo.py
    # either stream-copy into it or re-encode to h264/aac, which is always valid
    # content for this container regardless of what the source's own format was —
    # simpler than trying to match the source's extension, and the re-encode fallback
    # already recovers automatically if a stream copy into it fails.
    extension = ".m4a" if source_asset.asset_type == TYPE_AUDIO else ".mp4"

    with storage.materialise(source_asset.storage_key) as source_path:
        with tempfile.TemporaryDirectory(prefix="gam-subvideo-") as workdir:
            target_path = Path(workdir) / f"subvideo{extension}"
            extract_subvideo(source_path, target_path, in_point, out_point)

            progress("Reading metadata", 60, "")
            result = probe(target_path)
            preview = thumbnails.generate(
                target_path,
                source_asset.asset_type,
                source_asset.original_name or "",
                duration_seconds=result.duration_seconds,
            )

            progress("Saving", 80, "")
            stored = storage.write_file(new_key(source_asset.user_id, extension), target_path)
            thumb_key = None
            if preview:
                thumb = storage.write_bytes(thumb_key_for(stored.key), preview)
                thumb_key = thumb.key

    detail = f"{out_point - in_point:.0f}s sub-video"

    if mode == "promote":
        asset_service.promote_clip(session, clip, stored=stored, thumb_key=thumb_key, probe_result=result)
        return detail, None

    created = asset_service.create_subvideo_asset(
        session,
        source_asset=source_asset,
        stored=stored,
        thumb_key=thumb_key,
        in_point=in_point,
        out_point=out_point,
        name=data.get("name"),
        probe_result=result,
    )
    return detail, created.id
