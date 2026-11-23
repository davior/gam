"""Tag logic, kept out of the routers.

The two things here that are not CRUD are the recursive descent through the category
tree and the guard that stops that descent being infinite. Everything else is a lookup.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import Iterable, Optional

from sqlalchemy import text
from sqlmodel import Session, col, delete, func, select

from app.models.asset import Asset
from app.models.tag import AssetTag, Tag, TagCategory

logger = logging.getLogger(__name__)

# A category tree deeper than this is a mistake, not a taxonomy. The recursive CTE is
# already cycle-proof, so this exists to bound a pathological-but-acyclic chain rather
# than to prevent a hang.
MAX_CATEGORY_DEPTH = 32


# ─── tags ────────────────────────────────────────────────────────────────────


def normalise(name: str) -> str:
    """Trim, collapse internal whitespace, keep the case.

    Case survives because the user typed it and will read it back; it is uniqueness that
    is case-insensitive, enforced by the index rather than by mangling the value.
    """
    return " ".join((name or "").split())


def find(session: Session, user_id: str, name: str) -> Optional[Tag]:
    """The existing tag with this name, matched without regard to case."""
    cleaned = normalise(name)
    if not cleaned:
        return None
    return session.exec(
        select(Tag).where(
            Tag.user_id == user_id,
            func.lower(col(Tag.name)) == cleaned.lower(),
        )
    ).first()


def get_or_create(
    session: Session, user_id: str, name: str, *, category_id: Optional[str] = None
) -> Optional[Tag]:
    """Reuse the tag if it exists, otherwise make it. Returns None for a blank name.

    The lookup is what makes "nato" attach to "NATO" instead of hitting the unique index
    and raising. Relying on the index alone would be correct but would turn every
    ordinary re-use of a tag into an error path.
    """
    cleaned = normalise(name)
    if not cleaned:
        return None

    existing = find(session, user_id, cleaned)
    if existing:
        return existing

    tag = Tag(user_id=user_id, name=cleaned, category_id=category_id)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


def usage_counts(session: Session, user_id: str) -> dict[str, int]:
    """How many assets carry each of this user's tags, in one query.

    One query rather than one per tag: a tag list is rendered whole, and the count is
    what makes it prunable, so the N+1 would be on the hottest path that uses it.
    """
    rows = session.exec(
        select(AssetTag.tag_id, func.count(col(AssetTag.asset_id)))
        .join(Tag, col(Tag.id) == col(AssetTag.tag_id))
        .where(Tag.user_id == user_id)
        .group_by(col(AssetTag.tag_id))
    ).all()
    return {tag_id: count for tag_id, count in rows}


def asset_ids_for_tag(session: Session, tag_id: str) -> list[str]:
    """Every asset carrying this tag. Used to re-index after a rename or a delete."""
    return list(session.exec(select(AssetTag.asset_id).where(AssetTag.tag_id == tag_id)).all())


def delete_tag(session: Session, tag: Tag) -> list[str]:
    """Remove a tag and every attachment of it. Returns the assets that were affected.

    The caller re-indexes those assets: a deleted tag that stays in `tags_text` keeps
    answering searches for a word the library no longer contains.
    """
    asset_ids = asset_ids_for_tag(session, tag.id)
    session.exec(delete(AssetTag).where(col(AssetTag.tag_id) == tag.id))
    session.delete(tag)
    session.commit()
    return asset_ids


class TagNameClash(ValueError):
    """A rename onto a name another of the person's tags already has."""

    def __init__(self, existing: Tag):
        super().__init__(f"You already have a tag called {existing.name}")
        self.existing = existing


def rename_tag(session: Session, tag: Tag, new_name: str) -> bool:
    """Rename a tag, or refuse with `TagNameClash`. True if the name changed.

    A change of case alone ("nato" to "NATO") is a rename like any other: the unique index
    is case-insensitive, so the tag finds itself as the clash and that is not one. The
    caller re-indexes the tag's assets on True, or the old word keeps answering searches.
    Does not commit, so a caller doing several things commits them together.
    """
    cleaned = normalise(new_name)
    if not cleaned:
        raise ValueError("A tag name cannot be blank")
    clash = find(session, tag.user_id, cleaned)
    if clash is not None and clash.id != tag.id:
        raise TagNameClash(clash)
    if cleaned == tag.name:
        return False
    tag.name = cleaned
    session.add(tag)
    return True


def merge_tags(session: Session, sources: list[Tag], target: Tag) -> list[str]:
    """Fold the source tags into the target: every asset that carried a source carries the
    target, and the sources are gone. Returns the assets whose tags changed.

    The new rows skip the assets that already carry the target, because the join's
    composite primary key would refuse the duplicate and fail the whole flush; an asset
    tagged with both "nato" and "NATO" simply ends with one. A target with no category takes the
    first source's, so merging into a new spelling does not unfile the tag.
    """
    source_ids = [tag.id for tag in sources if tag.id != target.id]
    if not source_ids:
        return []
    affected = list(session.exec(
        select(AssetTag.asset_id).where(col(AssetTag.tag_id).in_(source_ids)).distinct()
    ).all())

    held = set(session.exec(select(AssetTag.asset_id).where(AssetTag.tag_id == target.id)).all())
    session.add_all(AssetTag(asset_id=asset_id, tag_id=target.id)
                    for asset_id in affected if asset_id not in held)
    # One transaction with the delete and the commit below, so a merge that fails part way
    # leaves both tags as they were rather than an asset carrying neither.
    session.flush()
    session.exec(delete(AssetTag).where(col(AssetTag.tag_id).in_(source_ids)))
    if target.category_id is None:
        filed = next((tag.category_id for tag in sources if tag.category_id), None)
        if filed:
            target.category_id = filed
            session.add(target)
    for tag in sources:
        if tag.id != target.id:
            session.delete(tag)
    session.commit()
    return affected


def asset_names_for_tags(
    session: Session, tag_ids: Iterable[str], limit: int = 5
) -> tuple[int, list[str]]:
    """How many assets carry any of these tags, and the names of the first few.

    What a proposal shows before it is applied: "14 assets: Interview with ..., ...". The
    count and the sample are of distinct assets, so an asset carrying two of the tags
    being merged is one asset, which is what the person will see change.
    """
    ids = list(tag_ids)
    if not ids:
        return 0, []
    carrying = select(AssetTag.asset_id).where(col(AssetTag.tag_id).in_(ids))
    count = session.exec(
        select(func.count()).select_from(Asset).where(col(Asset.id).in_(carrying))
    ).one()
    names = session.exec(
        select(Asset.name).where(col(Asset.id).in_(carrying)).order_by(col(Asset.name)).limit(limit)
    ).all()
    return int(count), list(names)


_PUNCTUATION = re.compile(r"[^\w\s]")
# Below this length a one-letter difference is a different word ("cat"/"car"), not a typo.
TYPO_MIN_LENGTH = 5
TYPO_RATIO = 0.88


def fold(name: str) -> str:
    """The form two spellings of one tag share: no case, accents or punctuation, and a
    naive singular. Naive on purpose: it is for proposing merges a person then reads, so a
    false pair costs an untick, and a missed one costs nothing."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or name
    plain = " ".join(_PUNCTUATION.sub("", plain.lower()).split())
    words = plain.split(" ")
    last = words[-1]
    if len(last) > 4 and last.endswith("ies"):
        last = last[:-3] + "y"
    elif len(last) > 4 and last.endswith(("ches", "shes", "sses", "xes")):
        last = last[:-2]
    elif len(last) > 3 and last.endswith("s") and not last.endswith("ss"):
        last = last[:-1]
    words[-1] = last
    return " ".join(words)


def similar_groups(tags: list[Tag]) -> list[list[Tag]]:
    """Tags that look like spellings of one another, in groups of two or more.

    Two passes: the same folded form ("Interviews", "interview"), then near misses between
    folded forms long enough for a typo to be one ("intervew"). The second is quadratic,
    which over a personal vocabulary of hundreds is nothing; joined by union-find so a
    chain of near misses lands in one group rather than several overlapping ones.
    """
    parent: dict[str, str] = {tag.id: tag.id for tag in tags}

    def root(tag_id: str) -> str:
        while parent[tag_id] != tag_id:
            parent[tag_id] = parent[parent[tag_id]]
            tag_id = parent[tag_id]
        return tag_id

    def join(a: str, b: str) -> None:
        parent[root(a)] = root(b)

    by_fold: dict[str, str] = {}
    folded = {tag.id: fold(tag.name) for tag in tags}
    for tag in tags:
        key = folded[tag.id]
        if key in by_fold:
            join(tag.id, by_fold[key])
        else:
            by_fold[key] = tag.id

    keys = [k for k in by_fold if len(k) >= TYPO_MIN_LENGTH]
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if abs(len(a) - len(b)) > 2:
                continue
            if difflib.SequenceMatcher(None, a, b).ratio() >= TYPO_RATIO:
                join(by_fold[a], by_fold[b])

    groups: dict[str, list[Tag]] = {}
    for tag in tags:
        groups.setdefault(root(tag.id), []).append(tag)
    return [group for group in groups.values() if len(group) > 1]


def detach_all_from_asset(session: Session, asset_id: str) -> None:
    """Drop every tag attachment belonging to one asset.

    Called when the asset itself is deleted. This is not merely tidiness: `AssetTag`
    declares `asset_id` as a foreign key with no `ON DELETE` action, and the engine
    turns `PRAGMA foreign_keys=ON` on for every connection, so SQLite *refuses* to
    delete an asset that still carries a tag. Without this the delete raises
    `FOREIGN KEY constraint failed` and the caller returns a 500.

    Does not commit: the caller deletes the asset in the same transaction, and a
    commit here would leave the row untagged if that delete then failed.
    """
    session.exec(delete(AssetTag).where(col(AssetTag.asset_id) == asset_id))


# ─── attachment ──────────────────────────────────────────────────────────────


def tags_for(session: Session, asset_id: str) -> list[Tag]:
    return list(
        session.exec(
            select(Tag)
            .join(AssetTag, col(AssetTag.tag_id) == col(Tag.id))
            .where(AssetTag.asset_id == asset_id)
            .order_by(col(Tag.name))
        ).all()
    )


def tags_for_many(session: Session, asset_ids: Iterable[str]) -> dict[str, list[Tag]]:
    """Every listed asset's tags in one query.

    The library renders sixty assets a page and each one shows its tags. Asking per
    asset would be sixty queries that grow with the page, which is invisible on a
    development library of five and miserable on a real one.
    """
    ids = list(asset_ids)
    if not ids:
        return {}

    rows = session.exec(
        select(AssetTag.asset_id, Tag)
        .join(Tag, col(Tag.id) == col(AssetTag.tag_id))
        .where(col(AssetTag.asset_id).in_(ids))
        .order_by(col(Tag.name))
    ).all()

    grouped: dict[str, list[Tag]] = {asset_id: [] for asset_id in ids}
    for asset_id, tag in rows:
        grouped.setdefault(asset_id, []).append(tag)
    return grouped


def attach(session: Session, asset_id: str, tag_id: str) -> bool:
    """Link a tag to an asset. True if this changed anything.

    Attaching twice is not an error — the UI can send a tag the asset already has, and
    the composite primary key makes the duplicate impossible anyway, so the check here
    is about not raising rather than about correctness.
    """
    existing = session.get(AssetTag, (asset_id, tag_id))
    if existing:
        return False
    session.add(AssetTag(asset_id=asset_id, tag_id=tag_id))
    session.commit()
    return True


def detach(session: Session, asset_id: str, tag_id: str) -> bool:
    existing = session.get(AssetTag, (asset_id, tag_id))
    if not existing:
        return False
    session.delete(existing)
    session.commit()
    return True


def tags_text_for(session: Session, asset_id: str) -> str:
    """What the keyword index stores for this asset's tags.

    Space-joined names, which is all FTS5 needs to tokenise them. This is called from
    `services.assets._reindex`, so every write path that touches tags keeps search in
    step by doing what it already does.
    """
    return " ".join(tag.name for tag in tags_for(session, asset_id))


# ─── categories ──────────────────────────────────────────────────────────────


def would_create_cycle(
    session: Session, user_id: str, category_id: str, new_parent_id: Optional[str]
) -> bool:
    """True if reparenting would make a category its own ancestor.

    This is the guard, not the belt. `descendant_category_ids` is written so a cycle
    that somehow exists still terminates, but a cycle in the data is a corrupt tree
    whatever the query does with it — it makes a category unreachable from the root and
    unrenderable as a tree — so the write is refused here instead.
    """
    if new_parent_id is None:
        return False
    if new_parent_id == category_id:
        return True

    seen: set[str] = {category_id}
    current: Optional[str] = new_parent_id
    for _ in range(MAX_CATEGORY_DEPTH):
        if current is None:
            return False
        if current in seen:
            return True
        seen.add(current)
        row = session.exec(
            select(TagCategory).where(
                TagCategory.id == current, TagCategory.user_id == user_id
            )
        ).first()
        if row is None:
            return False
        current = row.parent_category_id
    # Deeper than MAX_CATEGORY_DEPTH without reaching a root. Treat it as a cycle:
    # refusing a legitimate 32-deep taxonomy is a far better failure than accepting a
    # loop.
    return True


def category_paths(categories: Iterable[TagCategory]) -> dict[str, str]:
    """Every category's full path, "People > Scientists", from the flat rows.

    Bounded by MAX_CATEGORY_DEPTH rather than trusting the tree: a cycle that somehow got
    in would otherwise make this loop forever on a request that only wanted to list tags.
    """
    rows = {c.id: c for c in categories}
    paths: dict[str, str] = {}
    for category in rows.values():
        names, current, steps = [], category, 0
        while current is not None and steps < MAX_CATEGORY_DEPTH:
            names.append(current.name)
            current = rows.get(current.parent_category_id) if current.parent_category_id else None
            steps += 1
        paths[category.id] = " > ".join(reversed(names))
    return paths


def descendant_category_ids(session: Session, user_id: str, root_id: str) -> list[str]:
    """A category and everything beneath it.

    Filtering the library by "People" must find things filed under "People > Scientists",
    or nesting is decoration. SQLite walks the tree in one statement; doing it in Python
    would be a query per level.

    `UNION` rather than `UNION ALL` is deliberate and is the only thing standing between
    a corrupt tree and a request that never returns: UNION discards rows it has already
    produced, so a cycle runs out of new ids and stops. `would_create_cycle` is supposed
    to make that unreachable, but a query that hangs the worker is not a failure mode to
    leave resting on one guard.
    """
    rows = session.execute(
        text(
            """
            WITH RECURSIVE subtree(id) AS (
                SELECT id FROM tagcategory WHERE id = :root AND user_id = :user_id
                UNION
                SELECT c.id FROM tagcategory c
                JOIN subtree s ON c.parent_category_id = s.id
                WHERE c.user_id = :user_id
            )
            SELECT id FROM subtree
            """
        ),
        {"root": root_id, "user_id": user_id},
    ).all()
    return [row[0] for row in rows]


def delete_category(session: Session, user_id: str, category: TagCategory) -> None:
    """Remove a category, orphaning its children and its tags rather than cascading.

    Deleting a category must not delete the tags filed under it. A tag is the thing the
    user curated; a category is only where they filed it. So both children and tags are
    lifted to the top level, which is recoverable by re-filing — where a cascade would
    silently take a hand-built vocabulary with it.
    """
    child_ids = [c for c in descendant_category_ids(session, user_id, category.id)]

    for tag in session.exec(select(Tag).where(col(Tag.category_id).in_(child_ids))).all():
        tag.category_id = None
        session.add(tag)

    for child in session.exec(
        select(TagCategory).where(col(TagCategory.parent_category_id) == category.id)
    ).all():
        child.parent_category_id = None
        session.add(child)

    session.delete(category)
    session.commit()


# ─── filtering ───────────────────────────────────────────────────────────────


def asset_ids_with_all_tags(session: Session, user_id: str, tag_names: list[str]) -> set[str]:
    """Assets carrying every one of these tags.

    AND rather than OR: filters narrow. Someone who picks "interview" and "2024" wants
    the ones that are both, and an OR would hand back a longer list than they started
    with — the opposite of what pressing a filter is for.
    """
    cleaned = [normalise(name) for name in tag_names if normalise(name)]
    if not cleaned:
        return set()

    matched: Optional[set[str]] = None
    for name in cleaned:
        tag = find(session, user_id, name)
        if tag is None:
            return set()  # an unknown tag matches nothing, so the whole AND is empty
        ids = {
            row
            for row in session.exec(select(AssetTag.asset_id).where(AssetTag.tag_id == tag.id))
        }
        matched = ids if matched is None else (matched & ids)
        if not matched:
            return set()
    return matched or set()


def asset_ids_in_category(session: Session, user_id: str, category_id: str) -> set[str]:
    """Assets tagged with anything filed under this category or below it."""
    category_ids = descendant_category_ids(session, user_id, category_id)
    if not category_ids:
        return set()

    return {
        row
        for row in session.exec(
            select(AssetTag.asset_id)
            .join(Tag, col(Tag.id) == col(AssetTag.tag_id))
            .where(Tag.user_id == user_id, col(Tag.category_id).in_(category_ids))
        )
    }


def owned_asset_ids(session: Session, user_id: str, asset_ids: Iterable[str]) -> list[str]:
    """Filter a caller-supplied list down to assets this user actually owns.

    Bulk endpoints take ids from the client, so this is the boundary that stops a
    selection being used to tag — and thereby learn about — somebody else's library.
    """
    ids = list(asset_ids)
    if not ids:
        return []
    return list(
        session.exec(
            select(Asset.id).where(Asset.user_id == user_id, col(Asset.id).in_(ids))
        ).all()
    )
