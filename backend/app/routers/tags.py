"""The tag and category catalogue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, col, select

from app.auth import CurrentUser
from app.database import get_session
from app.models.tag import Tag, TagCategory
from app.schemas import DataResponse, ListResponse
from app.schemas_tags import (
    TagCategoryCreate,
    TagCategoryRead,
    TagCategoryUpdate,
    TagCreate,
    TagRead,
    TagUpdate,
)
from app.services import assets as asset_service
from app.services import tags as service

router = APIRouter()


def _bad_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "bad_request", "message": message},
    )


def _not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "message": f"{what} not found"},
    )


def _owned_tag(tag_id: str, user_id: str, session: Session) -> Tag:
    tag = session.get(Tag, tag_id)
    # Someone else's tag is reported as missing rather than forbidden: "forbidden" would
    # confirm the id exists, which is more than a stranger should learn.
    if tag is None or tag.user_id != user_id:
        raise _not_found("Tag")
    return tag


def _owned_category(category_id: str, user_id: str, session: Session) -> TagCategory:
    category = session.get(TagCategory, category_id)
    if category is None or category.user_id != user_id:
        raise _not_found("Category")
    return category


def _to_read(tag: Tag, counts: dict[str, int] | None = None) -> TagRead:
    return TagRead(
        id=tag.id,
        name=tag.name,
        category_id=tag.category_id,
        asset_count=None if counts is None else counts.get(tag.id, 0),
    )


# ─── tags ────────────────────────────────────────────────────────────────────


@router.get("", response_model=ListResponse[TagRead])
def list_tags(
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> ListResponse[TagRead]:
    """Every tag this user has, with how many assets carry it.

    Unpaged on purpose. A personal tag vocabulary is tens to low hundreds of rows, the
    UI renders all of it to offer autocomplete, and paging a list whose whole job is to
    be complete would mean the picker could not see a tag the user definitely has.
    """
    rows = session.exec(
        select(Tag).where(Tag.user_id == user.id).order_by(col(Tag.name))
    ).all()
    counts = service.usage_counts(session, user.id)

    return ListResponse[TagRead](
        data=[_to_read(tag, counts) for tag in rows],
        total=len(rows),
        limit=len(rows),
        offset=0,
    )


@router.post("", response_model=DataResponse[TagRead], status_code=status.HTTP_201_CREATED)
def create_tag(
    payload: TagCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[TagRead]:
    """Create a tag, or hand back the one that already exists.

    Not a conflict: the caller wanted a tag with this name to exist, and it does. 201
    with the existing row is friendlier than making every client handle a 409 for the
    ordinary case of typing a tag they already use.
    """
    if payload.category_id:
        _owned_category(payload.category_id, user.id, session)

    tag = service.get_or_create(session, user.id, payload.name, category_id=payload.category_id)
    if tag is None:
        raise _bad_request("A tag name cannot be blank")
    return DataResponse[TagRead](data=_to_read(tag))


@router.patch("/{tag_id}", response_model=DataResponse[TagRead])
def update_tag(
    tag_id: str,
    payload: TagUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[TagRead]:
    """Rename a tag, or re-file it.

    A rename changes the word every tagged asset is findable by, so all of them are
    re-indexed. Skipping that would leave the old name answering searches and the new
    one answering nothing.
    """
    tag = _owned_tag(tag_id, user.id, session)
    fields = payload.model_dump(exclude_unset=True)

    renamed = False
    if "name" in fields and fields["name"]:
        cleaned = service.normalise(fields["name"])
        clash = service.find(session, user.id, cleaned)
        if clash and clash.id != tag.id:
            raise _bad_request(f"You already have a tag called {clash.name}")
        renamed = cleaned.lower() != tag.name.lower() or cleaned != tag.name
        tag.name = cleaned

    if "category_id" in fields:
        if fields["category_id"]:
            _owned_category(fields["category_id"], user.id, session)
        tag.category_id = fields["category_id"]

    session.add(tag)
    session.commit()
    session.refresh(tag)

    if renamed:
        asset_service.reindex_ids(session, service.asset_ids_for_tag(session, tag.id))

    return DataResponse[TagRead](data=_to_read(tag))


# response_model=None alongside the `-> None` annotation: FastAPI infers a response
# model from the return type, and NoneType is a type like any other, so it would try
# to give a 204 a body and refuse at import time.
@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_tag(
    tag_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> None:
    """Delete a tag everywhere it is used.

    The assets that carried it are re-indexed afterwards, or the deleted word keeps
    answering searches for something the library no longer contains.
    """
    tag = _owned_tag(tag_id, user.id, session)
    affected = service.delete_tag(session, tag)
    asset_service.reindex_ids(session, affected)


# ─── categories ──────────────────────────────────────────────────────────────


@router.get("/categories/all", response_model=ListResponse[TagCategoryRead])
def list_categories(
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> ListResponse[TagCategoryRead]:
    """The flat set; the client assembles the tree from `parent_category_id`.

    Flat rather than nested because the UI needs both shapes — a tree to browse and a
    flat list to pick a parent from — and one of those is derivable from the other.
    """
    rows = session.exec(
        select(TagCategory).where(TagCategory.user_id == user.id).order_by(col(TagCategory.name))
    ).all()
    return ListResponse[TagCategoryRead](
        data=[
            TagCategoryRead(id=c.id, name=c.name, parent_category_id=c.parent_category_id)
            for c in rows
        ],
        total=len(rows),
        limit=len(rows),
        offset=0,
    )


@router.post(
    "/categories",
    response_model=DataResponse[TagCategoryRead],
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    payload: TagCategoryCreate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[TagCategoryRead]:
    if payload.parent_category_id:
        _owned_category(payload.parent_category_id, user.id, session)

    category = TagCategory(
        user_id=user.id, name=payload.name, parent_category_id=payload.parent_category_id
    )
    session.add(category)
    session.commit()
    session.refresh(category)
    return DataResponse[TagCategoryRead](
        data=TagCategoryRead(
            id=category.id, name=category.name, parent_category_id=category.parent_category_id
        )
    )


@router.patch("/categories/{category_id}", response_model=DataResponse[TagCategoryRead])
def update_category(
    category_id: str,
    payload: TagCategoryUpdate,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> DataResponse[TagCategoryRead]:
    """Rename or reparent.

    The reparent is the one that can corrupt the tree: making a category a child of its
    own descendant produces a loop that is unreachable from the root and unrenderable,
    so it is refused here rather than tolerated downstream.
    """
    category = _owned_category(category_id, user.id, session)
    fields = payload.model_dump(exclude_unset=True)

    if "name" in fields and fields["name"]:
        category.name = fields["name"]

    if "parent_category_id" in fields:
        parent_id = fields["parent_category_id"]
        if parent_id:
            _owned_category(parent_id, user.id, session)
        if service.would_create_cycle(session, user.id, category.id, parent_id):
            raise _bad_request("A category cannot be moved inside itself")
        category.parent_category_id = parent_id

    session.add(category)
    session.commit()
    session.refresh(category)
    return DataResponse[TagCategoryRead](
        data=TagCategoryRead(
            id=category.id, name=category.name, parent_category_id=category.parent_category_id
        )
    )


@router.delete(
    "/categories/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    response_class=Response,
)
def delete_category(
    category_id: str,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> None:
    """Delete a category without taking its tags with it.

    Children and tags are lifted to the top level instead of cascading. A tag is what
    the user curated; the category is only where they filed it, and a cascade would
    delete a hand-built vocabulary as a side effect of tidying the shelves.
    """
    category = _owned_category(category_id, user.id, session)
    service.delete_category(session, user.id, category)
