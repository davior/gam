"""Generating media with fal.ai (M8): the model catalogue, and starting a generation.

Everyone reads the catalogue; only admins (`ADMIN_USERS`) change it. A generation is
checked here, in the order docs/m8-ai-generation.md fixes, and then queued — nothing in
a request touches fal. Whether fal accepts it is the job's first network call, and its
answer arrives the way every job's does: in the activity feed.

Every check that can be made without fal is made here, because a refusal now costs a
red line under the form and the same refusal from fal costs a queued job, a wait, and —
for some endpoints — a bill.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.auth import AdminUser, CurrentUser, is_admin
from app.clock import utcnow
from app.database import get_session
from app.generation import catalogue
from app.generation.catalogue import InvalidEntry, extra_params_of, options_of
from app.generation.request import (
    ROLE_BASE,
    ROLE_END_FRAME,
    GenerationRequest,
    InvalidGeneration,
    build_parameters,
    check_prompt,
    check_shape,
    name_from_prompt,
    without_app_fields,
)
from app.ingest.filetypes import PILLOW_READABLE, TYPE_IMAGE, extension_of
from app.jobs import enrichment as enrichment_jobs
from app.jobs.registry import KINDS
from app.models.asset import Asset
from app.models.generation import GenerationModel, new_generation_model_id
from app.models.job import KIND_GENERATE
from app.models.user import User
from app.routers.assets import get_storage
from app.schemas import DataResponse, ListResponse
from app.schemas_generate import (
    GenerationCreate,
    GenerationModelCreate,
    GenerationModelRead,
    GenerationModelUpdate,
    RegenerateRequest,
)
from app.schemas_jobs import ActivityJobRead
from app.services.assets import generation_of
from app.settings_store import FAL_API_KEY, has_setting
from app.storage import LocalStorage, StorageError

router = APIRouter()

# Catalogue columns a PATCH may set to null, and the ones whose null means "back to the
# default". Anything else sent as null is refused rather than guessed at.
_NULLABLE = frozenset(
    {"image_field", "end_image_field", "unit_price", "price_unit", "price_currency"}
)
_RESET_ON_NULL = {"note": "", "options": {}, "extra_params": {}}


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# ─── the catalogue ───────────────────────────────────────────────────────────


def _read(row: GenerationModel) -> GenerationModelRead:
    return GenerationModelRead(
        id=row.id,
        endpoint_id=row.endpoint_id,
        kind=row.kind,
        label=row.label,
        note=row.note or "",
        sort_order=row.sort_order,
        is_active=row.is_active,
        image_field=row.image_field,
        image_field_is_list=row.image_field_is_list,
        max_images=row.max_images,
        end_image_field=row.end_image_field,
        options=options_of(row),
        extra_params=extra_params_of(row),
        unit_price=row.unit_price,
        price_unit=row.price_unit,
        price_currency=row.price_currency,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _entry_of(row: GenerationModel) -> dict[str, Any]:
    """A stored row as the dict `catalogue.validate` checks, for merging a PATCH into."""
    return {
        "endpoint_id": row.endpoint_id,
        "kind": row.kind,
        "label": row.label,
        "note": row.note,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "image_field": row.image_field,
        "image_field_is_list": row.image_field_is_list,
        "max_images": row.max_images,
        "end_image_field": row.end_image_field,
        "options": options_of(row),
        "extra_params": extra_params_of(row),
        "unit_price": row.unit_price,
        "price_unit": row.price_unit,
        "price_currency": row.price_currency,
    }


def _validated(entry: dict[str, Any]) -> dict[str, Any]:
    # Trimmed before checking, so a pasted id with a trailing space is not refused for
    # a character nobody can see.
    for name in ("endpoint_id", "label", "image_field", "end_image_field", "price_unit"):
        if isinstance(entry.get(name), str):
            entry[name] = entry[name].strip() or None
    try:
        return catalogue.validate(entry)
    except InvalidEntry as exc:
        raise _error(422, "invalid_model_entry", str(exc)) from exc


def _apply(row: GenerationModel, entry: dict[str, Any]) -> None:
    for name, value in entry.items():
        if name in ("options", "extra_params"):
            value = json.dumps(value, sort_keys=True)
        elif name == "note":
            value = value or ""
        setattr(row, name, value)


def _require_unique(session: Session, endpoint_id: str, *, except_id: Optional[str] = None) -> None:
    existing = session.exec(
        select(GenerationModel).where(GenerationModel.endpoint_id == endpoint_id)
    ).first()
    if existing is not None and existing.id != except_id:
        raise _endpoint_exists(endpoint_id)


def _endpoint_exists(endpoint_id: str) -> HTTPException:
    return _error(409, "endpoint_exists", f"{endpoint_id} is already in the catalogue")


def _commit(session: Session, row: GenerationModel) -> None:
    """Commit, turning the unique index's refusal into the 409 the pre-check gives.

    The pre-check is what normally answers; this is for two admins saving the same id at
    once, where only the database can say who was first.
    """
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _endpoint_exists(row.endpoint_id) from exc
    session.refresh(row)


def _row(session: Session, model_id: str) -> GenerationModel:
    row = session.get(GenerationModel, model_id)
    if row is None:
        raise _error(404, "model_not_found", "No such model in the catalogue")
    return row


@router.get("/models", response_model=ListResponse[GenerationModelRead])
def list_models(
    user: CurrentUser,
    include_inactive: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> ListResponse[GenerationModelRead]:
    """The catalogue, ordered as the form lists it.

    `include_inactive` is for the admin editor; anyone else asking for it gets the active
    rows, since a switched-off model is not one they can use.
    """
    query = select(GenerationModel)
    if not (include_inactive and is_admin(user, session.get(User, user.id))):
        query = query.where(col(GenerationModel.is_active).is_(True))
    rows = session.exec(
        query.order_by(
            col(GenerationModel.kind), col(GenerationModel.sort_order), col(GenerationModel.label)
        )
    ).all()
    return ListResponse(data=[_read(r) for r in rows], total=len(rows), limit=len(rows), offset=0)


@router.post("/models", response_model=DataResponse[GenerationModelRead], status_code=201)
def create_model(
    payload: GenerationModelCreate,
    user: AdminUser,
    session: Session = Depends(get_session),
) -> DataResponse[GenerationModelRead]:
    entry = _validated(payload.model_dump())
    _require_unique(session, entry["endpoint_id"])

    row = GenerationModel(
        id=new_generation_model_id(),
        endpoint_id=entry["endpoint_id"],
        kind=entry["kind"],
        label=entry["label"],
    )
    _apply(row, entry)
    _commit(session, row)
    return DataResponse(data=_read(row))


@router.patch("/models/{model_id}", response_model=DataResponse[GenerationModelRead])
def update_model(
    model_id: str,
    payload: GenerationModelUpdate,
    user: AdminUser,
    session: Session = Depends(get_session),
) -> DataResponse[GenerationModelRead]:
    """Change exactly the fields the body names; an explicit null clears.

    Validated as the whole row it would leave behind, not field by field: changing
    `kind` to text → image while the row still has an `image_field` must be refused even
    though neither field alone is wrong.
    """
    row = _row(session, model_id)
    entry = _entry_of(row)
    for name in payload.model_fields_set:
        value = getattr(payload, name)
        if value is None:
            if name in _NULLABLE:
                entry[name] = None
            elif name in _RESET_ON_NULL:
                entry[name] = _RESET_ON_NULL[name]
            else:
                raise _error(422, "invalid_model_entry", f"{name} cannot be cleared")
        else:
            entry[name] = value

    entry = _validated(entry)
    if entry["endpoint_id"] != row.endpoint_id:
        _require_unique(session, entry["endpoint_id"], except_id=row.id)

    _apply(row, entry)
    row.updated_at = utcnow()
    _commit(session, row)
    return DataResponse(data=_read(row))


@router.delete(
    "/models/{model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_model(
    model_id: str,
    user: AdminUser,
    session: Session = Depends(get_session),
) -> None:
    """Remove a row. Assets made with it keep their `ai_model` — it is a string, not a
    reference — and only lose the ability to be regenerated, which says so."""
    row = _row(session, model_id)
    session.delete(row)
    session.commit()


# ─── generating ──────────────────────────────────────────────────────────────


def _require_fal_key(session: Session, user_id: str) -> None:
    if not has_setting(session, user_id, FAL_API_KEY):
        raise _error(400, "fal_key_missing", "Add a fal.ai API key in Settings to generate")


def _owned_asset(session: Session, user_id: str, asset_id: str) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None or asset.user_id != user_id:
        raise _error(404, "asset_not_found", "No such asset")
    return asset


def _require_usable_base(storage: LocalStorage, asset: Asset) -> None:
    """An image that owns a file Pillow can open — which is what the job will do.

    Opening reads only the header, so this is cheap; it catches a truncated upload or a
    file that was never really an image before a job is queued to fail on it.
    """
    if asset.asset_type != TYPE_IMAGE:
        raise _error(422, "invalid_base", f"“{asset.name}” is not an image")
    if not asset.storage_key:
        raise _error(422, "invalid_base", f"“{asset.name}” is a clip and has no file of its own")
    extension = extension_of(asset.storage_key)
    if extension not in PILLOW_READABLE:
        raise _error(
            422,
            "invalid_base",
            f"“{asset.name}” is a {extension.lstrip('.').upper() or 'nameless'} image, "
            "which cannot be used as a base",
        )
    try:
        with storage.materialise(asset.storage_key) as path, Image.open(path):
            pass
    except (StorageError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise _error(422, "invalid_base", f"“{asset.name}” could not be read as an image") from exc


def _checked_sources(
    session: Session,
    storage: LocalStorage,
    user_id: str,
    base_ids: list[str],
    end_frame_id: Optional[str],
) -> list[dict]:
    """Every base owned (404), then every base usable (422) — ownership first throughout,
    so a request is never told something about an asset it could not see."""
    roles = [(asset_id, ROLE_BASE) for asset_id in base_ids]
    if end_frame_id:
        roles.append((end_frame_id, ROLE_END_FRAME))

    owned = [(_owned_asset(session, user_id, asset_id), role) for asset_id, role in roles]
    for asset, _role in owned:
        _require_usable_base(storage, asset)
    return [{"asset_id": asset.id, "role": role, "name": asset.name} for asset, role in owned]


def _queue(session: Session, user_id: str, request: GenerationRequest) -> ActivityJobRead:
    job = enrichment_jobs.submit_library(
        session,
        user_id,
        KIND_GENERATE,
        model=request.endpoint_id,
        payload=json.dumps({"request": request.to_dict()}, sort_keys=True),
        asset_name=name_from_prompt(request.prompt),
    )
    return KINDS["enrichment"].to_activity(job)


@router.post("", response_model=DataResponse[ActivityJobRead], status_code=status.HTTP_202_ACCEPTED)
def start_generation(
    payload: GenerationCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[ActivityJobRead]:
    """Queue a generation. Returns the job, which the activity feed then follows."""
    _require_fal_key(session, user.id)

    row = session.get(GenerationModel, payload.model_id) if payload.model_id else None
    if row is None:
        raise _error(404, "model_not_found", "No such model in the catalogue")
    if not row.is_active:
        raise _error(
            409, "model_inactive", f"{row.label} has been switched off by an administrator"
        )

    try:
        prompt = check_prompt(payload.prompt)
        check_shape(row, len(payload.base_asset_ids), payload.end_frame_asset_id is not None)
        parameters = build_parameters(row, options_of(row), payload.params.model_dump())
    except InvalidGeneration as exc:
        raise _error(422, "invalid_generation", str(exc)) from exc

    sources = _checked_sources(
        session, storage, user.id, payload.base_asset_ids, payload.end_frame_asset_id
    )

    request = GenerationRequest(
        model_id=row.id,
        endpoint_id=row.endpoint_id,
        kind=row.kind,
        prompt=prompt,
        parameters=parameters,
        image_field=row.image_field,
        image_field_is_list=row.image_field_is_list,
        end_image_field=row.end_image_field,
        sources=sources,
    )
    return DataResponse(data=_queue(session, user.id, request))


@router.post(
    "/{asset_id}/regenerate",
    response_model=DataResponse[ActivityJobRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate(
    asset_id: str,
    user: CurrentUser,
    payload: Optional[RegenerateRequest] = None,
    session: Session = Depends(get_session),
    storage: LocalStorage = Depends(get_storage),
) -> DataResponse[ActivityJobRead]:
    """Make a generated asset again from what it recorded about itself.

    The stored parameters are resent as they are — catalogue defaults included, which is
    why they were stored — through the endpoint's *current* row for its field names. The
    seed is the exception: plain regenerate drops it so fal picks a new one, since a
    regeneration that reproduced the same picture would not be one; `reuse_seed` sends
    the one fal reported back.
    """
    options = payload or RegenerateRequest()
    asset = _owned_asset(session, user.id, asset_id)
    generation = generation_of(asset)
    if generation is None or not generation.kind:
        raise _error(
            409, "not_generated", "This asset was not generated, so there is nothing to regenerate"
        )

    _require_fal_key(session, user.id)

    row = session.exec(
        select(GenerationModel).where(
            GenerationModel.endpoint_id == generation.model,
            col(GenerationModel.is_active).is_(True),
        )
    ).first()
    if row is None or row.kind != generation.kind:
        raise _error(
            409,
            "model_unavailable",
            f"{generation.model} is no longer an active model in the catalogue",
        )

    for source in generation.sources:
        found = session.get(Asset, source.asset_id)
        if found is None or found.user_id != user.id:
            raise _error(
                409,
                "source_asset_missing",
                f"The base image “{source.name or source.asset_id}” has been deleted, "
                "so this cannot be made again",
            )

    base_ids = [s.asset_id for s in generation.sources if s.role == ROLE_BASE]
    end_ids = [s.asset_id for s in generation.sources if s.role == ROLE_END_FRAME]
    try:
        prompt = check_prompt(options.prompt if options.prompt is not None else generation.prompt)
        check_shape(row, len(base_ids), bool(end_ids))
    except InvalidGeneration as exc:
        raise _error(422, "invalid_generation", str(exc)) from exc

    sources = _checked_sources(session, storage, user.id, base_ids, end_ids[0] if end_ids else None)

    parameters = dict(generation.parameters)
    stored_seed = parameters.pop("seed", None)
    if options.reuse_seed and options_of(row)["supports_seed"]:
        seed = generation.seed if generation.seed is not None else stored_seed
        if seed is not None:
            parameters["seed"] = seed

    request = GenerationRequest(
        model_id=row.id,
        endpoint_id=row.endpoint_id,
        kind=row.kind,
        prompt=prompt,
        parameters=without_app_fields(parameters, row.image_field, row.end_image_field),
        image_field=row.image_field,
        image_field_is_list=row.image_field_is_list,
        end_image_field=row.end_image_field,
        sources=sources,
        regenerated_from=asset.id,
    )
    return DataResponse(data=_queue(session, user.id, request))
