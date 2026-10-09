"""The generate job: a request to fal.ai in, library assets out.

In order, each a stage the activity feed shows:

1. Preparing the base images — each normalised to a data URI (`bases.py`).
2. Submitting to fal.ai — and the request ids **committed to the payload before the
   first poll**. `recover_pending` re-runs a job from the top; one that finds a stored
   request resumes polling it rather than submitting, and paying for, a second.
3. Waiting in fal.ai's queue / Generating — polled every `generation_poll_seconds`
   until fal says COMPLETED, which is also how it reports a failure, or until GAM's own
   deadline. On that deadline, and on a user's cancel, fal is told to stop: a request
   abandoned here keeps running, and billing, there.
4. Downloading — each output streamed under the size cap (`download.py`).
5. Saving — through `ingest_file`, the path an upload takes, so it is probed,
   thumbnailed and indexed like anything else; then the `ai_*` provenance, and the
   prompt as name and description so search finds it by what was asked for. Each asset
   id is written to the payload as it is made, so a restart here finishes the job
   instead of saving the same image twice — and the index being saved is written
   *before* ingest starts, so a restart in the middle of one finds and discards the
   half-made copy rather than keeping it beside the finished one.
6. Recording the cost — once, guarded by `usage_recorded` in the same commit as the row.
   Also when saving fails: fal billed the request whatever became of its files, and an
   errored job is never run again to record it later.

Runs on a worker thread of the generation queue. `progress` is the job's reporter and
the cancellation checkpoint, so it is called on every poll and between download chunks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from sqlmodel import Session, col, select

from app.clock import utcnow
from app.config import settings
from app.generation import bases, fal, pricing
from app.generation.download import download
from app.generation.errors import FalUnavailable, GenerationError
from app.generation.request import GenerationRequest, name_from_prompt
from app.ingest.filetypes import SOURCE_AI
from app.jobs.runner import JobCancelled, set_fields
from app.models.asset import Asset
from app.models.generation import KIND_IMAGE_TO_VIDEO, KIND_TEXT_TO_IMAGE, GenerationModel
from app.models.job import EnrichmentJob
from app.models.usage import KIND_IMAGE, KIND_VIDEO
from app.providers.base import ProviderError
from app.services import assets as asset_service
from app.settings_store import load_fal_key
from app.storage import StorageError, build_storage
from app.usage import events as usage_events

logger = logging.getLogger(__name__)

Progress = Callable[[str, int, str], None]

# Looked up on the module at call time, so a test replaces the wait between polls and
# the clock it is measured against, and a thirty-minute timeout runs in milliseconds.
sleep = time.sleep
now = utcnow

STAGE_PREPARING = "Preparing the base images"
STAGE_SUBMITTING = "Submitting to fal.ai"
STAGE_QUEUED = "Waiting in fal.ai's queue"
STAGE_GENERATING = "Generating"
STAGE_DOWNLOADING = "Downloading"
STAGE_SAVING = "Saving"
STAGE_COST = "Recording the cost"

# How long a generation is expected to take, for a progress bar that creeps rather than
# sits still. Only shapes the curve; nothing waits on it.
_EXPECTED_SECONDS = {KIND_IMAGE_TO_VIDEO: 180.0}
_EXPECTED_SECONDS_DEFAULT = 20.0

# Report a download's progress at most this often, in bytes: each report is a write.
_REPORT_EVERY_BYTES = 4 * 1024 * 1024

# fal's seed is a JSON number; SQLite's INTEGER is 64-bit.
_MAX_SEED = 2**63 - 1


@dataclass
class GenerationResult:
    detail: str
    created_assets: list[Asset] = field(default_factory=list)


@dataclass
class _State:
    """The job's payload, decoded. Written back after every step that must survive."""

    request: GenerationRequest
    queued: Optional[fal.QueuedRequest] = None
    submitted_at: Optional[datetime] = None
    # What fal returned, trimmed to what the rest of the job reads. Kept so a restart
    # after the result was fetched does not depend on fal still serving it.
    result: Optional[dict] = None
    created_asset_ids: list[str] = field(default_factory=list)
    # The output index whose ingest has started and not yet been recorded above. Set on
    # a resumed run only if the process died inside `ingest_file`, which commits the
    # asset row before it probes and thumbnails it.
    ingesting: Optional[int] = None
    usage_recorded: bool = False

    @classmethod
    def load(cls, payload: Optional[str]) -> "_State":
        try:
            data = json.loads(payload or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("request"), dict):
            raise GenerationError("This generation job has no request to send")
        submitted = data.get("submitted_at")
        try:
            submitted_at = datetime.fromisoformat(submitted) if isinstance(submitted, str) else None
        except ValueError:
            submitted_at = None
        created = data.get("created_asset_ids")
        if not isinstance(created, list):
            created = []
        ingesting = data.get("ingesting")
        if isinstance(ingesting, bool) or not isinstance(ingesting, int):
            ingesting = None
        return cls(
            request=GenerationRequest.from_dict(data["request"]),
            queued=fal.QueuedRequest.from_dict(data.get("fal_request")),
            submitted_at=submitted_at,
            result=data.get("result") if isinstance(data.get("result"), dict) else None,
            created_asset_ids=[i for i in created if isinstance(i, str)],
            ingesting=ingesting,
            usage_recorded=bool(data.get("usage_recorded")),
        )

    def encode(self) -> str:
        data: dict[str, Any] = {
            "request": self.request.to_dict(),
            "created_asset_ids": self.created_asset_ids,
            "usage_recorded": self.usage_recorded,
        }
        if self.queued is not None:
            data["fal_request"] = self.queued.to_dict()
        if self.submitted_at is not None:
            data["submitted_at"] = self.submitted_at.isoformat()
        if self.result is not None:
            data["result"] = self.result
        if self.ingesting is not None:
            data["ingesting"] = self.ingesting
        return json.dumps(data, sort_keys=True)


def run(session: Session, job: EnrichmentJob, progress: Progress) -> GenerationResult:
    state = _State.load(job.payload)
    request = state.request

    api_key = load_fal_key(session, job.user_id)
    if not api_key:
        raise GenerationError("No fal.ai API key is configured. Add one in Settings.")

    if state.result is None:
        if state.queued is None:
            body = _request_body(session, job, request, progress)
            progress(STAGE_SUBMITTING, 10, "")
            state.queued = fal.submit(request.endpoint_id, body, api_key=api_key)
            state.submitted_at = now()
            _save(session, job, state)
            logger.info("Generation %s submitted to fal.ai as %s", job.id, state.queued.request_id)
        else:
            logger.info(
                "Generation %s resuming fal.ai request %s", job.id, state.queued.request_id
            )

        fetched = _await_result(state, request, api_key, progress)
        state.result = _trim(fetched)
        _save(session, job, state)

    outputs = state.result.get("outputs") or []
    if not outputs:
        raise GenerationError("fal.ai finished but returned nothing to download")

    try:
        created = _save_outputs(session, job, state, outputs, progress)
    except Exception:
        # fal billed this request whatever became of its files — a download that never
        # arrived, a cancel mid-way — and an errored or cancelled job is never run
        # again to record it later. Exception, not BaseException: a process that dies
        # here is resumed by `recover_pending`, and that run records it.
        session.rollback()
        saved = [a for a in (session.get(Asset, i) for i in state.created_asset_ids) if a]
        _record_cost_once(session, job, state, saved, api_key)
        raise

    progress(STAGE_COST, 97, "")
    _record_cost_once(session, job, state, created, api_key)

    noun = "video" if request.kind == KIND_IMAGE_TO_VIDEO else "image"
    count = len(created)
    return GenerationResult(
        detail=f"{count} {noun}{'' if count == 1 else 's'}",
        created_assets=created,
    )


# ─── submitting ──────────────────────────────────────────────────────────────


def _request_body(
    session: Session, job: EnrichmentJob, request: GenerationRequest, progress: Progress
) -> dict:
    """Catalogue defaults and choices first, the app's own fields last, so nothing in
    `parameters` can override the prompt or the images."""
    body = dict(request.parameters)

    if request.kind != KIND_TEXT_TO_IMAGE and request.image_field:
        base_ids = request.base_asset_ids
        progress(STAGE_PREPARING, 3, f"{len(base_ids)} image{'' if len(base_ids) == 1 else 's'}")
        storage = build_storage()
        uris = [_base_uri(session, storage, job.user_id, asset_id) for asset_id in base_ids]
        body[request.image_field] = uris if request.image_field_is_list else uris[0]
        end_frame = request.end_frame_asset_id
        if end_frame and request.end_image_field:
            body[request.end_image_field] = _base_uri(session, storage, job.user_id, end_frame)

    body["prompt"] = request.prompt
    return body


def _base_uri(session: Session, storage, user_id: str, asset_id: str) -> str:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.user_id != user_id or not asset.storage_key:
        raise GenerationError("A base image was deleted before this could run")
    try:
        with storage.materialise(asset.storage_key) as path:
            return bases.data_uri(
                path,
                max_edge=settings.generation_base_max_edge,
                max_bytes=settings.generation_max_data_uri_mb * 1024 * 1024,
            )
    except (StorageError, OSError) as exc:
        raise GenerationError(f"The file for the base image “{asset.name}” is missing") from exc


# ─── waiting ─────────────────────────────────────────────────────────────────


def _await_result(
    state: _State, request: GenerationRequest, api_key: str, progress: Progress
) -> fal.QueueResult:
    queued = state.queued
    submitted_at = state.submitted_at or now()
    minutes = settings.generation_timeout_minutes
    deadline = submitted_at + timedelta(minutes=minutes)
    expected = _EXPECTED_SECONDS.get(request.kind, _EXPECTED_SECONDS_DEFAULT)
    stage, percent, detail = STAGE_QUEUED, 15, ""

    try:
        while True:
            try:
                current = fal.status(queued, api_key=api_key)
                if current.status == fal.STATUS_COMPLETED:
                    # A failed run is reported as COMPLETED too, with the reason beside
                    # it — checked before fetching a result that is not there.
                    if current.error:
                        raise GenerationError(f"fal.ai could not generate this: {current.error}")
                    progress(STAGE_GENERATING, 70, "Finished")
                    return fal.result(queued, api_key=api_key)

                if current.status == fal.STATUS_IN_QUEUE:
                    stage, percent = STAGE_QUEUED, 15
                    detail = _queue_detail(current.queue_position)
                else:
                    # IN_PROGRESS, or a status fal's clients do not know — treated as
                    # still running, with the deadline as the backstop.
                    elapsed = max(0.0, (now() - submitted_at).total_seconds())
                    stage = STAGE_GENERATING
                    percent = 20 + int(50 * elapsed / (elapsed + expected))
                    detail = f"{int(elapsed) // 60}:{int(elapsed) % 60:02d} so far"
            except (FalUnavailable, ProviderError) as exc:
                # A status check that failed says nothing about the generation, which
                # is still running and still billed. Ask again until the deadline.
                logger.info("fal.ai status check failed for %s: %s", queued.request_id, exc)
                detail = "fal.ai is not answering; still trying"

            progress(stage, percent, detail)

            if now() >= deadline:
                fal.cancel(queued, api_key=api_key)
                raise GenerationError(f"fal.ai did not finish within {minutes} minutes")

            sleep(settings.generation_poll_seconds)
    except JobCancelled:
        fal.cancel(queued, api_key=api_key)
        raise


def _queue_detail(position: Optional[int]) -> str:
    if position is None:
        return ""
    if position <= 0:
        return "Next in line"
    return f"Position {position + 1} in the queue"


def _trim(fetched: fal.QueueResult) -> dict:
    """The parts of fal's answer the rest of the job reads, in a form that persists."""
    body = fetched.body
    outputs: list[dict] = []
    for key in ("images", "videos"):
        for item in body.get(key) or []:
            outputs.append(item)
    for key in ("image", "video"):
        if isinstance(body.get(key), dict):
            outputs.append(body[key])

    seed = body.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or abs(seed) > _MAX_SEED:
        seed = None
    return {
        "outputs": [
            {"url": item["url"], "content_type": _text_or_none(item.get("content_type"))}
            for item in outputs
            if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]
        ],
        "seed": seed,
        "billable_units": fetched.billable_units,
        "fal_request_id": fetched.request_id,
    }


def _text_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


# ─── saving ──────────────────────────────────────────────────────────────────


def _save_outputs(
    session: Session,
    job: EnrichmentJob,
    state: _State,
    outputs: list[dict],
    progress: Progress,
) -> list[Asset]:
    request = state.request
    # Made by an earlier run of this job before a restart. One the user has deleted
    # since is simply not there; it is not made again.
    created = [a for a in (session.get(Asset, i) for i in state.created_asset_ids) if a is not None]

    is_video = request.kind == KIND_IMAGE_TO_VIDEO
    default_extension = ".mp4" if is_video else ".png"
    total = len(outputs)
    storage = build_storage()

    with tempfile.TemporaryDirectory(prefix="gam-generate-") as workdir:
        for index, output in enumerate(outputs):
            if index < len(state.created_asset_ids):
                continue

            label = f"{index + 1} of {total} · " if total > 1 else ""
            start = 72 + int(16 * index / total)
            span = max(1, int(16 / total))
            progress(STAGE_DOWNLOADING, start, label.rstrip(" ·"))

            path = download(
                output["url"],
                Path(workdir),
                f"output-{index}",
                max_bytes=settings.generation_max_download_mb * 1024 * 1024,
                default_extension=default_extension,
                content_type_hint=output.get("content_type"),
                on_progress=_download_reporter(progress, label, start, span),
                # One file taking longer than a whole generation may is not arriving.
                max_seconds=settings.generation_timeout_minutes * 60,
            )

            progress(STAGE_SAVING, 90, label.rstrip(" ·"))
            if state.ingesting == index:
                _discard_half_saved(session, storage, job, state, path)
            state.ingesting = index
            _save(session, job, state)

            asset = _ingest(session, storage, job, request, state, path)
            created.append(asset)
            state.created_asset_ids.append(asset.id)
            state.ingesting = None
            _save(session, job, state)
    return created


def _discard_half_saved(
    session: Session, storage, job: EnrichmentJob, state: _State, path: Path
) -> None:
    """Remove the copy an interrupted run left of this output, before saving it again.

    `ingest_file` commits the row before it probes and thumbnails, so a process killed
    in between leaves an asset this job never recorded. It is found by its bytes — the
    same file fal is still serving — and only among this user's generated assets made
    since the job was queued, so an identical earlier generation (the same seed can
    reproduce an image exactly) is never mistaken for it.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    leftovers = session.exec(
        select(Asset).where(
            Asset.user_id == job.user_id,
            Asset.source == SOURCE_AI,
            Asset.checksum_sha256 == digest.hexdigest(),
            col(Asset.upload_date) >= job.created_at,
            col(Asset.id).not_in(state.created_asset_ids or [""]),
        )
    ).all()
    for leftover in leftovers:
        logger.info("Discarding %s, half-saved by an interrupted run of %s", leftover.id, job.id)
        asset_service.delete_asset(session, storage, leftover)


def _download_reporter(progress: Progress, label: str, start: int, span: int):
    last = {"bytes": -_REPORT_EVERY_BYTES}

    def report(received: int, total: Optional[int]) -> None:
        if received - last["bytes"] < _REPORT_EVERY_BYTES and received != total:
            return
        last["bytes"] = received
        megabytes = received / (1024 * 1024)
        if total:
            percent = start + int(span * received / total)
            detail = f"{label}{megabytes:.1f} of {total / (1024 * 1024):.1f} MB"
        else:
            percent = start
            detail = f"{label}{megabytes:.1f} MB"
        progress(STAGE_DOWNLOADING, percent, detail)

    return report


def _ingest(
    session: Session,
    storage,
    job: EnrichmentJob,
    request: GenerationRequest,
    state: _State,
    path: Path,
) -> Asset:
    name = name_from_prompt(request.prompt)
    try:
        asset = asset_service.ingest_file(
            session,
            storage,
            user_id=job.user_id,
            source_path=path,
            filename=_filename_for(name, path),
            name=name,
            source=SOURCE_AI,
        )
    except asset_service.UnsupportedFile as exc:
        raise GenerationError(
            f"fal.ai delivered a {path.suffix or 'nameless'} file, "
            "which the library does not accept"
        ) from exc

    asset.ai_model = request.endpoint_id
    asset.ai_generation_type = request.kind
    asset.ai_prompt = request.prompt
    asset.ai_parameters = json.dumps(request.parameters, sort_keys=True)
    asset.ai_source_assets = json.dumps(request.sources)
    asset.ai_seed = (state.result or {}).get("seed")
    asset.ai_generated_at = now()
    # Through the AI writer rather than set directly, for its stamp and its re-index:
    # `field_provenance` records that a model, not a person, wrote these two, and the
    # keyword index — built by ingest before there was a description — learns the
    # prompt. The `ai_*` columns above ride in on the same commit.
    asset_service.apply_ai_metadata(session, asset, {"name": name, "description": request.prompt})
    return asset


def _filename_for(name: str, path: Path) -> str:
    """The display filename: the name, with the real extension (which decides the
    type). Slashes replaced for the reason `import_url._filename_for` gives."""
    stem = name.replace("/", "-").replace("\\", "-").rstrip("…").strip() or "generated"
    return f"{stem[:200]}{path.suffix.lower()}"


# ─── the bill ────────────────────────────────────────────────────────────────


def _record_cost_once(
    session: Session, job: EnrichmentJob, state: _State, created: list[Asset], api_key: str
) -> None:
    if state.usage_recorded:
        return
    try:
        _record_cost(session, job, state, created, api_key)
    except Exception:  # noqa: BLE001 - accounting must not undo the work it measures
        # `usage_events.record` already holds this rule for the write; the price
        # lookup before it needs the same, or a pricing hiccup fails a generation
        # whose files are already in the library.
        session.rollback()
        logger.warning("Could not record the cost of generation %s", job.id, exc_info=True)


def _record_cost(
    session: Session, job: EnrichmentJob, state: _State, created: list[Asset], api_key: str
) -> None:
    request = state.request
    result = state.result or {}
    is_video = request.kind == KIND_IMAGE_TO_VIDEO

    row = session.exec(
        select(GenerationModel).where(GenerationModel.endpoint_id == request.endpoint_id)
    ).first()
    catalogue_price = (
        pricing.UnitPrice(
            unit_price=row.unit_price,
            unit=row.price_unit or "",
            currency=row.price_currency or "USD",
        )
        if row is not None and row.unit_price is not None and row.price_unit
        else None
    )

    cost = pricing.compute(
        is_video=is_video,
        billable_units=result.get("billable_units"),
        api_price=pricing.list_price(job.user_id, request.endpoint_id, api_key=api_key),
        catalogue_price=catalogue_price,
        fallback_unit=row.price_unit if row is not None else None,
        outputs=[
            pricing.OutputFacts(width=a.width, height=a.height, duration_seconds=a.duration_seconds)
            for a in created
        ],
    )

    # The flag goes into the same transaction as the row: `record` commits once, and
    # that commit carries this payload change with it. Two separate commits would leave
    # a window where a restart finds the row written and the flag not, and bills twice.
    # If `record` fails it rolls back both, which is the consistent outcome too.
    state.usage_recorded = True
    job.payload = state.encode()
    session.add(job)
    usage_events.record(
        session,
        user_id=job.user_id,
        asset_id=created[0].id if created else None,
        kind=KIND_VIDEO if is_video else KIND_IMAGE,
        units=cost.units,
        unit_type=cost.unit_type,
        provider="fal.ai",
        model=request.endpoint_id,
        cost=cost.cost,
        currency=cost.currency,
        cost_estimated=cost.estimated,
        # The id fal bills under; the queue's own id if the result did not carry one.
        external_ref=result.get("fal_request_id")
        or (state.queued.request_id if state.queued else None),
    )


def _save(session: Session, job: EnrichmentJob, state: _State) -> None:
    set_fields(session, job, payload=state.encode())


# ─── cancelling a job no worker holds ────────────────────────────────────────


def cancel_remote(session: Session, job: EnrichmentJob) -> None:
    """Tell fal to stop a request this job submitted, when no worker will.

    A running job sends the cancel itself, at its next checkpoint. A *queued* one with a
    request already at fal — recovered after a restart, waiting for one of the
    generation workers — never reaches a checkpoint once cancelled: the worker sees the
    status and returns before running it. Without this its request runs to the end
    there, billed. Best effort; never raises.
    """
    try:
        state = _State.load(job.payload)
    except GenerationError:
        return
    if state.queued is None or state.result is not None:
        return
    api_key = load_fal_key(session, job.user_id)
    if api_key:
        fal.cancel(state.queued, api_key=api_key)
