"""The assistant's tools: those that look, and those that propose.

None of them writes to the library. `propose_changes`, `propose_enrichment`, `propose_clip`,
`propose_cut`, `propose_grab`, `propose_playlist`, `propose_note` and the three `propose_tag_*` add to a
`Working` proposal that the turn ends with, and that is all they do. The person applies
it (routers/assistant.py) or throws it away, so a model that misreads a request costs a
discarded card and not a rewritten library.

Every handler opens its own short session. A turn is a minute of waiting on a model with
a few reads in between, and a session held across the waiting would pin a read
transaction on a database the rest of the app is writing to.

Ownership is checked in every tool, not once at the start: a model chooses the ids, and
an id it was never shown is still an id. Someone else's asset is reported as missing, the
way the REST API does, so a guessed id learns nothing.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from typing import Callable, ContextManager, Dict, Iterator, List, Optional

from gecko_suite_client import ToolSpec
from sqlmodel import Session, col, select

from app.config import settings
from app.enrichment.describe import describable
from app.enrichment.source import document_text
from app.enrichment.summarize import summarisable
from app.ingest.filetypes import TYPE_AUDIO, TYPE_VIDEO
from app.models.asset import Asset
from app.models.tag import Tag, TagCategory
from app.models.document import DocumentPage
from app.models.transcript import TranscriptSegment
from app.search import fts
from app.services import assets as asset_service
from app.services import pdf_grab
from app.services import playlists as playlist_service
from app.services import search as search_service
from app.storage import build_storage
from app.schemas_tags import MAX_CATEGORY_LENGTH, MAX_TAG_LENGTH
from app.services import tags as tag_service

# What a proposal may ask the enrichment pipeline to do once applied.
ENRICHMENTS = ("summarize", "describe", "autotag")

MAX_RESULTS = 20
NAME_MAX, TEXT_MAX = 255, 20_000

# Clips and cuts one turn may propose. A model asked for "every mention" of something can
# run away with it; past this it is told to stop and let the person look at what it has.
MAX_CLIPS = 25
REASON_MAX = 300
# Two proposals for the same moment (within this many seconds at both ends) are one.
SAME_RANGE_SECONDS = 0.5
# Notes for Gecko Notes one turn may propose, and how long one may be. A note is a
# summary or a report, not an export of the library.
MAX_NOTES = 3
NOTE_MAX = 50_000
# How a note links an asset: `[Name](/a/<id>)`. Made absolute at Apply, where GAM's own
# address is known (assistant/apply.py).
ASSET_LINK = re.compile(r"\]\(\s*/a/([A-Za-z0-9-]+)\s*\)")

# Playlist items one turn may propose, across every playlist it proposes. Higher than
# MAX_CLIPS because a playlist is the request for many moments, and each costs nothing
# until applied; past this the model is told to stop and say what is left.
MAX_PLAYLIST_ITEMS = 60

# Tag operations one turn may propose. Counted in operations, not tags: filing 300 tags into
# one group is one operation, so sorting a whole vocabulary costs about one per group.
MAX_TAG_OPS = 100
# Tags one filing, merge or delete may name. A vocabulary is hundreds, not thousands.
MAX_TAG_IDS = 500
# Tags list_tags hands over at a time; past this it says where to carry on.
TAG_PAGE = 200
# Groups of look-alike tags find_similar_tags reports at once.
MAX_SIMILAR_GROUPS = 50
# How far a walk up the proposed category tree goes before calling it a loop.
TREE_DEPTH = 64
# Matching the REST API's limits (schemas_tags), so a proposal never makes what a person
# could not.
TAG_NAME_MAX = MAX_TAG_LENGTH
CATEGORY_MAX = MAX_CATEGORY_LENGTH

SPECS: List[ToolSpec] = [
    ToolSpec(
        "search_assets",
        "Search the library by meaning and keywords, across names, descriptions, summaries, "
        "tags and transcripts. Returns the best moment in each asset that matched.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "type": {"type": "string", "enum": ["image", "video", "audio", "document"],
                     "description": "Only this kind of asset."},
            "limit": {"type": "integer", "description": f"Default 10, at most {MAX_RESULTS}."},
        }, "required": ["query"]}),
    ToolSpec(
        "get_asset",
        "Everything recorded about one asset: its name, description, summary, tags, "
        "attribution, size, and whether it has a transcript.",
        {"type": "object", "properties": {"asset_id": {"type": "string"}},
         "required": ["asset_id"]}),
    ToolSpec(
        "read_transcript",
        "The words spoken in a video or audio asset (or the text of a document), one line per "
        "segment as [start-end] text. Long ones come in parts: pass start_seconds to carry on. "
        "To look at just one stretch (to find where a passage begins and ends before clipping "
        "it), pass start_seconds and end_seconds.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string"},
            "start_seconds": {"type": "number", "description": "Where to start. Default 0."},
            "end_seconds": {"type": "number",
                            "description": "Stop after the segment that reaches this point."},
        }, "required": ["asset_id"]}),
    ToolSpec(
        "list_clips",
        "The clips and cut-out videos already made from one video or audio asset, with their "
        "ranges. Look here before proposing a clip, so as not to make one that exists.",
        {"type": "object", "properties": {"asset_id": {"type": "string"}},
         "required": ["asset_id"]}),
    ToolSpec(
        "list_tags",
        "The person's existing tags, most used first, each with its id, how many assets carry "
        "it and the category (group) it is filed under; the first page also lists the "
        "categories, nested as Parent > Child, with their ids. Look here before proposing a "
        "tag, and before organising tags. Comes in pages: pass offset to carry on.",
        {"type": "object", "properties": {
            "offset": {"type": "integer", "description": "Where to carry on from. Default 0."},
            "uncategorised_only": {"type": "boolean",
                                   "description": "Only tags not filed under any category."},
            "max_uses": {"type": "integer",
                         "description": "Only tags on at most this many assets (0: unused)."},
        }}),
    ToolSpec(
        "find_similar_tags",
        "Groups of the person's tags that look like spellings of one tag: different case, "
        "plurals, punctuation, small typos (interview / Interviews / intervew). Each with its "
        "id and how many assets carry it. Use it to find duplicates to merge. Similar is not "
        "the same: read each group before proposing a merge.",
        {"type": "object", "properties": {}}),
    ToolSpec(
        "web_search",
        "Search the web, for what the library cannot say: who someone is, when something "
        "happened, what a term means. Returns titles, links and short passages. Use it to "
        "inform a proposal or an answer, and say where a fact came from; it is not a way to "
        "find the person's own assets (use search_assets).",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
    ToolSpec(
        "list_playlists",
        "The person's playlists, with their ids and how many items each holds. Look here when "
        "asked to add to a playlist that already exists.",
        {"type": "object", "properties": {}}),
    ToolSpec(
        "propose_playlist",
        "Propose a playlist: a named, ordered list of videos, audio, images and moments of "
        "them, played one after another. Each item is an asset, whole or with a range in "
        "seconds (taken from the transcript, starting at the beginning of a sentence and "
        "ending at the end of one). The moments live in the playlist; no clips are made. "
        "Give playlist_id to add to an existing playlist (from list_playlists), otherwise "
        "a name for a new one. Call again with the same name or playlist_id to add more "
        "items. Nothing is made until the person applies the proposal.",
        {"type": "object", "properties": {
            "name": {"type": "string", "description": "The new playlist's name."},
            "playlist_id": {"type": "string",
                            "description": "An existing playlist to add to, instead of a new one."},
            "description": {"type": "string", "description": "One sentence on what it is."},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "asset_id": {"type": "string"},
                "start_seconds": {"type": "number",
                                  "description": "Omit both times for the whole asset."},
                "end_seconds": {"type": "number"},
                "label": {"type": "string", "description": "A short plain name for this moment."},
                "reason": {"type": "string",
                           "description": "One sentence on why it belongs, shown to the person."},
            }, "required": ["asset_id"]}},
        }, "required": ["items"]}),
    ToolSpec(
        "propose_note",
        "Propose a new note in Gecko Notes, the person's notes app: a summary, a report or "
        "a list written from what is in the library. Write it in Markdown (headings, lists, "
        "tables). Link each asset it mentions as [Name](/a/<asset_id>), which becomes a link "
        "to that asset in GAM. Nothing is written until the person applies the proposal; it "
        "then lands in their \"From GAM\" folder, or the folder they asked for.",
        {"type": "object", "properties": {
            "title": {"type": "string"},
            "markdown": {"type": "string", "description": "The whole note, in Markdown."},
            "folder": {"type": "string",
                       "description": "Only when the person names a folder for it."},
        }, "required": ["title", "markdown"]}),
    ToolSpec(
        "propose_changes",
        "Propose edits to one asset's name, description, summary or tags. Nothing is changed "
        "until the person applies the proposal. Call it once per asset; a second call for the "
        "same asset replaces the fields it names. Give only the fields that should change.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "summary": {"type": "string"},
            "add_tags": {"type": "array", "items": {"type": "string"}},
            "remove_tags": {"type": "array", "items": {"type": "string"},
                            "description": "Tags the asset has now and should lose."},
        }, "required": ["asset_id"]}),
    ToolSpec(
        "propose_enrichment",
        "Propose running the library's own AI passes on an asset (a fresh summary, a "
        "description of what an image or video shows, tag suggestions). Use when the asset "
        "has no description or summary yet, or a transcript has changed.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string"},
            "actions": {"type": "array", "items": {"type": "string", "enum": list(ENRICHMENTS)}},
        }, "required": ["asset_id", "actions"]}),
    ToolSpec(
        "propose_clip",
        "Propose a clip: a named window onto part of a video or audio asset, made instantly "
        "with no new file (the original is not touched, and the clip plays that stretch of "
        "it). Use this for 'find the part where...', 'clip that', 'mark the moments'. Nothing "
        "is made until the person applies the proposal. Give the range in seconds, taken "
        "from the transcript's timestamps: start at the beginning of a sentence and end at "
        "the end of one.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string", "description": "The video or audio to clip."},
            "start_seconds": {"type": "number"},
            "end_seconds": {"type": "number"},
            "name": {"type": "string", "description": "A short plain name for the clip."},
            "reason": {"type": "string",
                       "description": "One sentence on why this stretch, shown to the person."},
        }, "required": ["asset_id", "start_seconds", "end_seconds"]}),
    ToolSpec(
        "find_pages",
        "Find which pages of one PDF (or other paged document) talk about something. Returns "
        "page numbers with a snippet of each. Use it to turn 'the table about signals' into "
        "a page number before propose_grab; try other words if the first query finds nothing. "
        "Scanned PDFs have no text to search.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string", "description": "The document to look in."},
            "query": {"type": "string", "description": "Words that would be on the page."},
        }, "required": ["asset_id", "query"]}),
    ToolSpec(
        "read_page",
        "The full text of one page of a document, to check it is the page wanted before "
        "proposing a grab of it.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string"},
            "page": {"type": "integer", "description": "The page number, counted from 1."},
        }, "required": ["asset_id", "page"]}),
    ToolSpec(
        "propose_grab",
        "Propose a screen grab of one page of a PDF, saved as a picture in the library that "
        "can then go in a playlist or over a video. Use it for 'get me the cover page', 'grab "
        "the page with the table of signals'. Find the page with find_pages and check it with "
        "read_page first, unless the person named it ('page 3', 'the front page' is page 1). "
        "Nothing is made until the person applies the proposal. A whole page only: it cannot "
        "crop to a table.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string", "description": "The PDF."},
            "page": {"type": "integer", "description": "The page number, counted from 1."},
            "name": {"type": "string", "description": "A short plain name for the picture."},
            "reason": {"type": "string",
                       "description": "One sentence on why this page, shown to the person."},
        }, "required": ["asset_id", "page"]}),
    ToolSpec(
        "propose_cut",
        "Propose cutting a stretch out of a video or audio asset into a NEW video (or audio) "
        "file of its own: a real cut that takes a moment to make and uses disk space. Use it "
        "only when the person asks for a new video, a cut-out, an export or a file they can "
        "use on its own; for 'find' or 'clip' use propose_clip. Nothing is made until the "
        "person applies the proposal. Ranges as for propose_clip.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string", "description": "The video or audio to cut from."},
            "start_seconds": {"type": "number"},
            "end_seconds": {"type": "number"},
            "name": {"type": "string", "description": "A short plain name for the new video."},
            "reason": {"type": "string",
                       "description": "One sentence on why this stretch, shown to the person."},
        }, "required": ["asset_id", "start_seconds", "end_seconds"]}),
    ToolSpec(
        "propose_tag_groups",
        "Propose organising tags into groups. Groups are categories, which nest (People > "
        "Scientists); each tag is filed under at most one. In one call: new categories, "
        "existing categories moved under another or renamed, and tags filed into a category. "
        "Name a category by its id from list_tags (or its path, People > Scientists, when that "
        "names one), or by the ref of one proposed in this turn (a new one's ref is its name "
        "unless you give one). Nothing changes until the person "
        "applies the proposal. Call again to add more.",
        {"type": "object", "properties": {
            "categories": {"type": "array", "description": "New categories.", "items": {
                "type": "object", "properties": {
                    "name": {"type": "string"},
                    "parent": {"type": "string",
                               "description": "Category id or ref to nest it under; omit for top level."},
                    "ref": {"type": "string",
                            "description": "What to call it in this turn, if not its name."},
                }, "required": ["name"]}},
            "moves": {"type": "array", "description": "Existing categories to move or rename.",
                      "items": {"type": "object", "properties": {
                          "category_id": {"type": "string"},
                          "parent": {"type": ["string", "null"],
                                     "description": "New parent id or ref; null for top level; omit to leave it."},
                          "new_name": {"type": "string"},
                      }, "required": ["category_id"]}},
            "file": {"type": "array", "description": "Tags to file under a category.",
                     "items": {"type": "object", "properties": {
                         "tag_ids": {"type": "array", "items": {"type": "string"}},
                         "category": {"type": ["string", "null"],
                                      "description": "Category id or ref; null takes them out of any."},
                     }, "required": ["tag_ids", "category"]}},
        }}),
    ToolSpec(
        "propose_tag_rename",
        "Propose renaming tags, or merging several into one, across every asset that carries "
        "them. A rename onto a name another tag already has is a merge. A merge moves every "
        "asset from the source tags onto the target and deletes the sources; merge into the "
        "most used spelling. Nothing changes until the person applies the proposal.",
        {"type": "object", "properties": {
            "renames": {"type": "array", "items": {"type": "object", "properties": {
                "tag_id": {"type": "string"},
                "new_name": {"type": "string"},
            }, "required": ["tag_id", "new_name"]}},
            "merges": {"type": "array", "items": {"type": "object", "properties": {
                "source_tag_ids": {"type": "array", "items": {"type": "string"}},
                "target_tag_id": {"type": "string", "description": "An existing tag to keep."},
                "target_name": {"type": "string",
                                "description": "Or a name to merge into, new or existing."},
            }, "required": ["source_tag_ids"]}},
        }}),
    ToolSpec(
        "propose_tag_delete",
        "Propose deleting tags everywhere they are used. Meant for unused tags and junk; a "
        "tag still on assets is better merged into another, and if asked to delete one, say "
        "how many assets lose it. Nothing changes until the person applies the proposal.",
        {"type": "object", "properties": {
            "tag_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["tag_ids"]}),
]

# Tools that only look. The worker reports a step for the rest.
READS = frozenset({"search_assets", "get_asset", "read_transcript", "list_clips", "list_tags",
                   "find_similar_tags", "list_playlists", "web_search", "find_pages", "read_page"})

# What a web search may hand back to the model in one call. A page of results, not a crawl.
MAX_WEB_CHARS = 6_000


def _fail(message: str) -> tuple:
    return message, True


def _clean(value: object) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _precise(seconds: float) -> str:
    """m:ss.t, to a tenth. Transcript lines carry this so that a range the model reads off
    one is a range it can cut: whole seconds would clip the last word of a sentence."""
    tenths = max(0, int(seconds * 10))
    whole, tenth = divmod(tenths, 10)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    stamp = f"{minutes}:{secs:02d}.{tenth}"
    return f"{hours}:{minutes:02d}:{secs:02d}.{tenth}" if hours else stamp


def _seconds(value: object) -> Optional[float]:
    """A point in time as seconds, from a number or from the m:ss the transcript shows.

    Models are asked for seconds and told the transcript's labels are m:ss.t, and a model
    that hands back "1:02.4" has understood the request: refusing it would cost a turn to
    teach it a unit it already used correctly.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None
    if isinstance(value, str):
        total = 0.0
        try:
            for part in value.strip().split(":"):
                total = total * 60 + float(part)
        except ValueError:
            return None
        return total
    return None


class Working:
    """One turn's tools and the proposal they build."""

    def __init__(self, user_id: str, open_session: Callable[[], ContextManager[Session]],
                 on_step: Callable[[str, str], None] = lambda kind, text: None):
        self.user_id = user_id
        self._open = open_session
        self._step = on_step
        # asset_id -> the proposal's entry for it, in the order they were first proposed.
        self.items: Dict[str, dict] = {}
        # Clips and cuts proposed this turn, in order. Each has its own `clip_id`, since
        # several can be proposed from one asset and the person may take only some.
        self.clips: List[dict] = []
        # Page grabs proposed this turn, each with its own `grab_id`.
        self.grabs: List[dict] = []
        # Playlists proposed this turn, each with its own `playlist_ref` so the person can
        # take one and not another.
        self.playlists: List[dict] = []
        # Notes for Gecko Notes, each with its own `note_ref`.
        self.notes: List[dict] = []
        # Changes to the tag vocabulary: categories made, moved and filed into, tags renamed,
        # merged and deleted. In proposal order; apply runs them in dependency order.
        self.tag_ops: List[dict] = []
        # A new category's ref, as the model names it (lowercased), -> its op.
        self._category_refs: Dict[str, dict] = {}
        # Set by the worker once it holds a connection to the suite: `query -> results`.
        # Unset, the tool says so instead of failing.
        self.search: Optional[Callable[[str], list]] = None

    # ─── dispatch ────────────────────────────────────────────────────────────

    @property
    def handlers(self) -> Dict[str, Callable[[dict], object]]:
        return {
            "search_assets": self.search_assets,
            "get_asset": self.get_asset,
            "read_transcript": self.read_transcript,
            "list_clips": self.list_clips,
            "list_tags": self.list_tags,
            "find_similar_tags": self.find_similar_tags,
            "list_playlists": self.list_playlists,
            "web_search": self.web_search,
            "propose_changes": self.propose_changes,
            "propose_enrichment": self.propose_enrichment,
            "propose_clip": self.propose_clip,
            "propose_cut": self.propose_cut,
            "find_pages": self.find_pages,
            "read_page": self.read_page,
            "propose_grab": self.propose_grab,
            "propose_playlist": self.propose_playlist,
            "propose_note": self.propose_note,
            "propose_tag_groups": self.propose_tag_groups,
            "propose_tag_rename": self.propose_tag_rename,
            "propose_tag_delete": self.propose_tag_delete,
        }

    @contextmanager
    def _session(self) -> Iterator[Session]:
        with self._open() as session:
            yield session

    def _owned(self, session: Session, asset_id: object) -> Optional[Asset]:
        asset = session.get(Asset, asset_id) if isinstance(asset_id, str) and asset_id else None
        return asset if asset is not None and asset.user_id == self.user_id else None

    # ─── looking ─────────────────────────────────────────────────────────────

    def search_assets(self, args: dict) -> object:
        query = _clean(args.get("query"))
        if not query:
            return _fail("Give a query to search for.")
        limit = args.get("limit")
        limit = min(MAX_RESULTS, limit) if isinstance(limit, int) and limit > 0 else 10
        kind = args.get("type") if args.get("type") in ("image", "video", "audio", "document") else None
        self._step("search", f"Searching the library: {query!r}")
        with self._session() as session:
            outcome = search_service.search(session, self.user_id, query, limit=limit,
                                            asset_type=kind)
            assets = search_service.load_assets(
                session, self.user_id, [hit.asset_id for hit in outcome.assets])
            if not outcome.assets:
                return "Nothing in the library matched."
            lines = []
            for hit in outcome.assets:
                asset = assets.get(hit.asset_id)
                if asset is None:
                    continue
                where = f" at {_clock(hit.start_time)}" if hit.start_time is not None else ""
                if getattr(hit, "page_number", None):
                    where = f" on page {hit.page_number}"
                more = f" (+{hit.other_matches} more moments)" if hit.other_matches else ""
                snippet = " ".join((hit.snippet or "").split())[:240]
                lines.append(f"- {asset.id} | {asset.asset_type} | {asset.name}{where}{more}"
                             + (f"\n    {snippet}" if snippet else ""))
        note = "" if outcome.semantic_ran else "\n(Keyword matches only: meaning-based search is not available.)"
        return "\n".join(lines) + note

    def get_asset(self, args: dict) -> object:
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            self._step("read", f"Looking at {asset.name}")
            names = [t.name for t in tag_service.tags_for(session, asset.id)]
            fields = [
                ("id", asset.id), ("name", asset.name), ("type", asset.asset_type),
                ("description", asset.description), ("summary", asset.summary),
                ("tags", ", ".join(names) or None),
                ("duration", _clock(asset.duration_seconds) if asset.duration_seconds else None),
                ("transcript", asset.transcript_status),
                ("pages", self._page_count(asset)),
                ("creator", asset.creator), ("publisher", asset.publisher),
                ("source title", asset.source_title), ("published", asset.published_date),
                ("source url", asset.source_url), ("license", asset.license),
            ]
            return "\n".join(f"{label}: {value}" for label, value in fields if value)

    @staticmethod
    def _page_count(asset: Asset) -> Optional[int]:
        if not pdf_grab.is_pdf(asset):
            return None
        try:
            return pdf_grab.page_count(build_storage(), asset)
        except Exception:  # noqa: BLE001 - a damaged PDF just has no count to report
            return None

    def find_pages(self, args: dict) -> object:
        query = _clean(args.get("query"))
        if not query:
            return _fail("Give words that would be on the page.")
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            self._step("search", f"Looking for {query!r} in {asset.name}")
            hits = fts.search_pages(session, self.user_id, query, limit=8, asset_id=asset.id)
            if not hits:
                has_text = session.exec(
                    select(DocumentPage.id).where(DocumentPage.asset_id == asset.id)
                ).first()
                if has_text is None:
                    return ("This document has no readable text, so its pages cannot be searched. "
                            "It may be scanned. Ask the person which page they mean.")
                return "No page matched. Try other words from the page."
            pages = {
                row.id: row for row in session.exec(
                    select(DocumentPage).where(col(DocumentPage.id).in_([h.page_id for h in hits])))
            }
            lines = []
            for hit in hits:
                page = pages.get(hit.page_id)
                if page is None or page.page_number is None:
                    continue
                snippet = " ".join((hit.snippet or "").split())[:240]
                lines.append(f"- page {page.page_number}" + (f": {snippet}" if snippet else ""))
        return "\n".join(lines) or "No page matched. Try other words from the page."

    def read_page(self, args: dict) -> object:
        page_number = args.get("page")
        if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
            return _fail("Give the page as a number from 1.")
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            self._step("read", f"Reading page {page_number} of {asset.name}")
            row = session.exec(
                select(DocumentPage).where(DocumentPage.asset_id == asset.id,
                                           DocumentPage.page_number == page_number)
            ).first()
            if row is None or not (row.text or "").strip():
                return (f"Page {page_number} has no readable text. It may be a picture, or "
                        "scanned, or past the end of the document.")
            cap = settings.assistant_transcript_chars
            return row.text[:cap] + ("\n[cut off]" if len(row.text) > cap else "")

    def read_transcript(self, args: dict) -> object:
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            self._step("read", f"Reading the transcript of {asset.name}")
            start = _seconds(args.get("start_seconds"))
            start = start if start is not None and start > 0 else 0.0
            end = _seconds(args.get("end_seconds"))
            query = select(TranscriptSegment).where(TranscriptSegment.asset_id == asset.id)
            if start > 0:
                # Strictly after: a segment that ends exactly where the window begins is
                # the one before it, and handing it over again repeats a line on every
                # "carry on from here".
                query = query.where(TranscriptSegment.end_time > start)
            if end is not None and end > start:
                query = query.where(TranscriptSegment.start_time <= end)
            segments = session.exec(query.order_by(col(TranscriptSegment.idx))).all()
            if not segments:
                text = document_text(session, asset) if start == 0 else ""
                if not text:
                    return ("This asset has no transcript"
                            + (f" (status: {asset.transcript_status})." if asset.transcript_status else "."))
                cap = settings.assistant_transcript_chars
                return text[:cap] + ("\n[cut off]" if len(text) > cap else "")
            cap, used, lines = settings.assistant_transcript_chars, 0, []
            for segment in segments:
                text = (segment.text or "").strip()
                if not text:
                    continue
                line = f"[{_precise(segment.start_time)}–{_precise(segment.end_time)}] {text}"
                if used + len(line) > cap and lines:
                    return "\n".join(lines) + (
                        f"\n[more follows: call again with start_seconds={int(segment.start_time)}]")
                lines.append(line)
                used += len(line) + 1
            return "\n".join(lines)

    def web_search(self, args: dict) -> object:
        query = _clean(args.get("query"))
        if not query:
            return _fail("Give a query to search the web for.")
        if self.search is None:
            return _fail("Web search is not available right now.")
        self._step("search", f"Searching the web: {query!r}")
        try:
            results = self.search(query)
        except Exception as exc:  # noqa: BLE001 - a failed search is information for the model
            return _fail(f"The web search failed: {exc}")
        from gecko_suite_client import format_results

        text = format_results(results)
        return text[:MAX_WEB_CHARS] + ("\n[cut off]" if len(text) > MAX_WEB_CHARS else "")

    def list_clips(self, args: dict) -> object:
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            self._step("read", f"Looking at the clips of {asset.name}")
            children = asset_service.list_children(session, asset.id, self.user_id)
            if not children:
                return "No clips or cut-out videos have been made from this asset."
            lines = []
            for child in children:
                # A clip with no file of its own is a live window; one with a file is a
                # real video made from a cut (or a clip made into one).
                kind = "live clip" if child.storage_key is None else "cut-out file"
                if child.in_point is None or child.out_point is None:
                    where = ""
                else:
                    where = f" | {_precise(child.in_point)}–{_precise(child.out_point)}"
                lines.append(f"- {child.id} | {kind}{where} | {child.name}")
            return "\n".join(lines)

    def list_tags(self, args: dict) -> object:
        offset = args.get("offset")
        offset = offset if isinstance(offset, int) and offset > 0 else 0
        max_uses = args.get("max_uses")
        max_uses = max_uses if isinstance(max_uses, int) and not isinstance(max_uses, bool) \
            and max_uses >= 0 else None
        unfiled_only = args.get("uncategorised_only") is True
        with self._session() as session:
            counts = tag_service.usage_counts(session, self.user_id)
            rows = session.exec(select(Tag).where(Tag.user_id == self.user_id)).all()
            categories = session.exec(
                select(TagCategory).where(TagCategory.user_id == self.user_id)).all()
        if not rows and not categories:
            return "There are no tags yet."
        paths = tag_service.category_paths(categories)
        unfiled = sum(1 for t in rows if t.category_id not in paths)
        lines = [f"{len(rows)} tag(s), {unfiled} not in any category; "
                 f"{len(categories)} categor{'y' if len(categories) == 1 else 'ies'}."]
        if offset == 0 and categories:
            lines.append("Categories:")
            lines += [f"- {cid} | {path}" for cid, path in sorted(paths.items(), key=lambda p: p[1].lower())]

        chosen = [t for t in rows
                  if (not unfiled_only or t.category_id not in paths)
                  and (max_uses is None or counts.get(t.id, 0) <= max_uses)]
        chosen.sort(key=lambda t: (-counts.get(t.id, 0), t.name.lower()))
        page = chosen[offset:offset + TAG_PAGE]
        if not page:
            lines.append("No tags match." if offset == 0 else "No more tags.")
            return "\n".join(lines)
        lines.append(f"Tags {offset + 1}-{offset + len(page)} of {len(chosen)} "
                     "(id | name (assets) | category):")
        for t in page:
            where = f" | {paths[t.category_id]}" if t.category_id in paths else ""
            lines.append(f"- {t.id} | {t.name} ({counts.get(t.id, 0)}){where}")
        if offset + len(page) < len(chosen):
            lines.append(f"[more: call again with offset={offset + len(page)}]")
        return "\n".join(lines)

    def find_similar_tags(self, args: dict) -> object:
        with self._session() as session:
            counts = tag_service.usage_counts(session, self.user_id)
            rows = list(session.exec(select(Tag).where(Tag.user_id == self.user_id)).all())
        self._step("read", "Looking for tags that are spellings of one another")
        groups = tag_service.similar_groups(rows)
        if not groups:
            return "No tags look like spellings of one another."
        for group in groups:
            group.sort(key=lambda t: (-counts.get(t.id, 0), t.name.lower()))
        groups.sort(key=lambda g: -sum(counts.get(t.id, 0) for t in g))
        lines = [" | ".join(f"{t.name} {t.id} ({counts.get(t.id, 0)})" for t in group)
                 for group in groups[:MAX_SIMILAR_GROUPS]]
        more = len(groups) - MAX_SIMILAR_GROUPS
        return ("Each line is one group, most used first: name id (assets).\n"
                + "\n".join(f"- {line}" for line in lines)
                + (f"\n[{more} more groups not shown]" if more > 0 else ""))

    def list_playlists(self, args: dict) -> object:
        from app.models.playlist import Playlist

        with self._session() as session:
            rows = session.exec(select(Playlist).where(Playlist.user_id == self.user_id)
                                .order_by(col(Playlist.updated_at).desc()).limit(100)).all()
            if not rows:
                return "There are no playlists yet."
            summaries = playlist_service.summaries(session, rows)
            self._step("read", "Looking at the playlists")
            return "\n".join(f"- {p.id} | {p.name} | {summaries[p.id]['count']} item(s)"
                             for p in rows)

    # ─── proposing ───────────────────────────────────────────────────────────

    def propose_changes(self, args: dict) -> object:
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            current_tags = [t.name for t in tag_service.tags_for(session, asset.id)]
            held = {t.lower() for t in current_tags}
            entry = self._entry(asset, current_tags)

            changed, notes = {}, []
            for field, limit in (("name", NAME_MAX), ("description", TEXT_MAX), ("summary", TEXT_MAX)):
                if field not in args:
                    continue
                value = _clean(args[field])
                if value is None:
                    notes.append(f"{field} left alone: it can't be blank.") if field == "name" else \
                        notes.append(f"{field} left alone: clearing it is not something to propose.")
                elif len(value) > limit:
                    return _fail(f"The {field} is too long ({len(value)} of {limit} characters).")
                elif value == (getattr(asset, field) or "").strip():
                    notes.append(f"{field} is already that.")
                else:
                    changed[field] = value

            adds = self._tag_names(args.get("add_tags"))
            adds = [t for t in adds if t.lower() not in held]
            removes = [t for t in self._tag_names(args.get("remove_tags")) if t.lower() in held]
            if set(map(str.lower, adds)) & set(map(str.lower, removes)):
                return _fail("A tag can't be both added and removed.")
            if args.get("remove_tags") and not removes:
                notes.append("None of the tags to remove are on this asset.")

            if not (changed or adds or removes):
                return "Nothing to change." + ("".join(f" {n}" for n in notes))
            # A second call for the same asset replaces what it names and keeps the rest.
            entry.update(changed)
            if "add_tags" in args:
                entry["add_tags"] = adds
            if "remove_tags" in args:
                entry["remove_tags"] = removes
            self.items[asset.id] = entry
        self._step("propose", f"Proposing changes to {entry['asset_name']}")
        return "Recorded. " + self._describe(entry) + "".join(f" {n}" for n in notes)

    def propose_enrichment(self, args: dict) -> object:
        actions = [a for a in (args.get("actions") or []) if a in ENRICHMENTS]
        if not actions:
            return _fail(f"Name at least one of: {', '.join(ENRICHMENTS)}.")
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            eligible = describable if "describe" in actions else summarisable
            if not (summarisable(asset) and eligible(asset)):
                return _fail("This asset has no file to run those on.")
            entry = self._entry(asset, [t.name for t in tag_service.tags_for(session, asset.id)])
            entry["enrich"] = sorted(set(actions), key=ENRICHMENTS.index)
            self.items[asset.id] = entry
        self._step("propose", f"Proposing {', '.join(entry['enrich'])} for {entry['asset_name']}")
        return f"Recorded: {', '.join(entry['enrich'])} for {entry['asset_name']}."

    def propose_clip(self, args: dict) -> object:
        return self._propose_range("clip", args)

    def propose_cut(self, args: dict) -> object:
        return self._propose_range("cut", args)

    def _propose_range(self, kind: str, args: dict) -> object:
        """Record a clip or a cut of one range, after the checks apply would make anyway.

        Checked here as well as at apply so the model hears about a bad range while it can
        still correct it, and the person is never shown a card that cannot be applied. The
        library may change in between, so apply checks again.
        """
        start, end = _seconds(args.get("start_seconds")), _seconds(args.get("end_seconds"))
        if start is None or end is None:
            return _fail("Give start_seconds and end_seconds, as numbers of seconds.")
        if len(self.clips) >= MAX_CLIPS:
            return _fail(f"That is {MAX_CLIPS} proposals already, the most one request may "
                         "make. Say what is left and let the person ask for more.")
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            problem = asset_service.cut_problem(asset, start, end)
            if problem:
                return _fail(problem)
            name = _clean(args.get("name"))
            if name and len(name) > NAME_MAX:
                return _fail(f"The name is too long ({len(name)} of {NAME_MAX} characters).")
            reason = _clean(args.get("reason"))
            reason = reason[:REASON_MAX] if reason else None
            for held in self.clips:
                if (held["asset_id"] == asset.id and held["kind"] == kind
                        and abs(held["in_point"] - start) < SAME_RANGE_SECONDS
                        and abs(held["out_point"] - end) < SAME_RANGE_SECONDS):
                    return f"Already proposed: {_precise(start)}–{_precise(end)} of {asset.name}."
            entry = {
                "clip_id": str(uuid.uuid4()), "kind": kind,
                "asset_id": asset.id, "asset_name": asset.name,
                "in_point": start, "out_point": end,
                "name": name, "reason": reason,
            }
            self.clips.append(entry)
        what = "a clip" if kind == "clip" else "a new video"
        self._step("propose", f"Proposing {what} from {entry['asset_name']} "
                              f"({_precise(start)}–{_precise(end)})")
        return (f"Recorded {what}: {entry['asset_name']} {_precise(start)}–{_precise(end)}"
                + (f", called {name!r}." if name else "."))

    def propose_grab(self, args: dict) -> object:
        """Record a grab of one PDF page, after the checks apply would make anyway."""
        page = args.get("page")
        if not isinstance(page, int) or isinstance(page, bool):
            return _fail("Give the page as a whole number, counted from 1.")
        if len(self.grabs) >= MAX_CLIPS:
            return _fail(f"That is {MAX_CLIPS} grabs already, the most one request may "
                         "make. Say what is left and let the person ask for more.")
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            problem = pdf_grab.grab_problem(build_storage(), asset, page)
            if problem:
                return _fail(problem)
            name = _clean(args.get("name"))
            if name and len(name) > NAME_MAX:
                return _fail(f"The name is too long ({len(name)} of {NAME_MAX} characters).")
            reason = _clean(args.get("reason"))
            reason = reason[:REASON_MAX] if reason else None
            for held in self.grabs:
                if held["asset_id"] == asset.id and held["page"] == page:
                    return f"Already proposed: page {page} of {asset.name}."
            entry = {
                "grab_id": str(uuid.uuid4()),
                "asset_id": asset.id, "asset_name": asset.name,
                "page": page, "name": name, "reason": reason,
            }
            self.grabs.append(entry)
        self._step("propose", f"Proposing a picture of page {page} of {entry['asset_name']}")
        return (f"Recorded a picture of page {page} of {entry['asset_name']}"
                + (f", called {name!r}." if name else "."))

    def propose_playlist(self, args: dict) -> object:
        """Record a playlist, or more items for one already proposed this turn.

        Items are checked one by one and the good ones kept: a model that got one range
        wrong out of twelve should hear which, and fix or drop it, without redoing the
        other eleven. Every item goes through `playlists.resolve`, the rule a person's own
        additions meet, and apply checks again.
        """
        raw_items = args.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            return _fail("Give at least one item.")
        name = _clean(args.get("name"))
        if name and len(name) > NAME_MAX:
            return _fail(f"The name is too long ({len(name)} of {NAME_MAX} characters).")
        target_id = _clean(args.get("playlist_id"))

        with self._session() as session:
            existing = None
            if target_id:
                existing = playlist_service.owned(session, self.user_id, target_id)
                if existing is None:
                    return _fail("No such playlist. Use an id from list_playlists, or give a "
                                 "name for a new one.")
            elif not name:
                return _fail("Give a name for the new playlist, or the playlist_id of one to add to.")
            entry = self._playlist_entry(existing, name, _clean(args.get("description")))

            used = sum(len(p["items"]) for p in self.playlists)
            added, problems = [], []
            for index, raw in enumerate(raw_items, start=1):
                if not isinstance(raw, dict):
                    problems.append(f"item {index}: not an object.")
                    continue
                if used + len(added) >= MAX_PLAYLIST_ITEMS:
                    problems.append(f"item {index} and after: that is {MAX_PLAYLIST_ITEMS} items "
                                    "already, the most one request may propose. Say what is left.")
                    break
                start, end = raw.get("start_seconds"), raw.get("end_seconds")
                start = _seconds(start) if start is not None else None
                end = _seconds(end) if end is not None else None
                if (raw.get("start_seconds") is not None and start is None) or \
                        (raw.get("end_seconds") is not None and end is None):
                    problems.append(f"item {index}: give the times as numbers of seconds.")
                    continue
                try:
                    resolved = playlist_service.resolve(session, self.user_id, raw.get("asset_id"),
                                                        start, end)
                except playlist_service.PlaylistProblem as exc:
                    problems.append(f"item {index}: {exc.message}")
                    continue
                if self._already_listed(entry, resolved):
                    problems.append(f"item {index}: already in this playlist.")
                    continue
                label = _clean(raw.get("label"))
                reason = _clean(raw.get("reason"))
                item = {
                    "asset_id": resolved.asset.id, "asset_name": resolved.asset.name,
                    "asset_type": resolved.asset.asset_type,
                    "in_point": resolved.in_point, "out_point": resolved.out_point,
                    "label": label[:playlist_service.LABEL_MAX] if label else None,
                    "reason": reason[:REASON_MAX] if reason else None,
                }
                entry["items"].append(item)
                added.append(item)

            if added and entry not in self.playlists:
                self.playlists.append(entry)
        if not added:
            return _fail("Nothing was added. " + " ".join(problems))
        where = f"added to {entry['name']}" if entry["playlist_id"] else f"in new playlist {entry['name']!r}"
        self._step("propose", f"Proposing {len(added)} item(s) {where}")
        return (f"Recorded {len(added)} item(s) {where} ({len(entry['items'])} in all)."
                + (" Not added: " + " ".join(problems) if problems else ""))

    def propose_note(self, args: dict) -> object:
        """Record a note for Gecko Notes. Its asset links are checked now, while the model
        can still fix them: a link to an id it guessed would land in the note as a link to
        nothing, or to an asset it was never shown."""
        title = _clean(args.get("title"))
        markdown = args.get("markdown") if isinstance(args.get("markdown"), str) else ""
        if not markdown.strip():
            return _fail("Give the note's text, in Markdown.")
        if not title:
            return _fail("Give the note a title.")
        if len(title) > NAME_MAX:
            return _fail(f"The title is too long ({len(title)} of {NAME_MAX} characters).")
        if len(markdown) > NOTE_MAX:
            return _fail(f"The note is too long ({len(markdown)} of {NOTE_MAX} characters). "
                         "Make it shorter, or split it into notes.")
        held = next((n for n in self.notes if n["title"].lower() == title.lower()), None)
        if held is None and len(self.notes) >= MAX_NOTES:
            return _fail(f"That is {MAX_NOTES} notes already, the most one request may make.")
        ids = set(ASSET_LINK.findall(markdown))
        with self._session() as session:
            unknown = sorted(i for i in ids if self._owned(session, i) is None)
        if unknown:
            return _fail("These linked ids are not assets in the library: "
                         + ", ".join(unknown) + ". Use ids from search_assets.")
        folder = _clean(args.get("folder"))
        entry = {"note_ref": held["note_ref"] if held else str(uuid.uuid4()),
                 "title": title, "markdown": markdown,
                 "folder": folder[:120] if folder else None, "asset_ids": sorted(ids)}
        if held:
            # The same title again is a rewrite of that note, not a second one.
            self.notes[self.notes.index(held)] = entry
        else:
            self.notes.append(entry)
        self._step("propose", f"Proposing a note in Gecko Notes: {title}")
        return (f"Recorded the note {title!r} ({len(markdown)} characters, "
                f"{len(ids)} linked asset(s)).")

    # ─── the tag vocabulary ──────────────────────────────────────────────────

    def propose_tag_groups(self, args: dict) -> object:
        """Record new categories, moves of existing ones, and tags filed into them.

        Done in that order within the call, so a category made in it can be the parent of
        another or the home of tags in the same call. Each entry is checked on its own and
        the good ones kept, as for playlist items; the model hears which failed and why.
        """
        creates = args.get("categories") if isinstance(args.get("categories"), list) else []
        moves = args.get("moves") if isinstance(args.get("moves"), list) else []
        files = args.get("file") if isinstance(args.get("file"), list) else []
        if not (creates or moves or files):
            return _fail("Give categories to make, moves, or tags to file.")
        done, problems = [], []
        with self._session() as session:
            for index, raw in enumerate(creates, start=1):
                said = self._create_category(session, raw) if isinstance(raw, dict) else _fail("not an object.")
                (problems.append(f"category {index}: {said[0]}") if isinstance(said, tuple)
                 else done.append(said))
            for index, raw in enumerate(moves, start=1):
                said = self._move_category(session, raw) if isinstance(raw, dict) else _fail("not an object.")
                (problems.append(f"move {index}: {said[0]}") if isinstance(said, tuple)
                 else done.append(said))
            for index, raw in enumerate(files, start=1):
                said = self._file_tags(session, raw) if isinstance(raw, dict) else _fail("not an object.")
                (problems.append(f"filing {index}: {said[0]}") if isinstance(said, tuple)
                 else done.append(said))
        return self._tag_reply(done, problems)

    def propose_tag_rename(self, args: dict) -> object:
        renames = args.get("renames") if isinstance(args.get("renames"), list) else []
        merges = args.get("merges") if isinstance(args.get("merges"), list) else []
        if not (renames or merges):
            return _fail("Give renames or merges.")
        done, problems = [], []
        with self._session() as session:
            for index, raw in enumerate(renames, start=1):
                said = self._rename(session, raw) if isinstance(raw, dict) else _fail("not an object.")
                (problems.append(f"rename {index}: {said[0]}") if isinstance(said, tuple)
                 else done.append(said))
            for index, raw in enumerate(merges, start=1):
                said = self._merge(session, raw) if isinstance(raw, dict) else _fail("not an object.")
                (problems.append(f"merge {index}: {said[0]}") if isinstance(said, tuple)
                 else done.append(said))
        return self._tag_reply(done, problems)

    def propose_tag_delete(self, args: dict) -> object:
        ids = self._ids(args.get("tag_ids"))
        if not ids:
            return _fail("Give the ids of the tags to delete, from list_tags.")
        done, problems = [], []
        with self._session() as session:
            for tag_id in ids:
                tag = self._owned_tag(session, tag_id)
                if tag is None:
                    problems.append(f"{tag_id}: no such tag.")
                    continue
                if self._claimed(tag.id, "delete_tag"):
                    problems.append(f"{tag.name}: already being renamed or merged in this proposal.")
                    continue
                if any(op["kind"] == "delete_tag" and op["tag_id"] == tag.id for op in self.tag_ops):
                    continue
                if self._full():
                    problems.append(self._full_sentence())
                    break
                count, sample = tag_service.asset_names_for_tags(session, [tag.id])
                self._add_op({"kind": "delete_tag", "tag_id": tag.id, "name": tag.name,
                              "asset_count": count, "sample_assets": sample})
                done.append(f"delete {tag.name}" + (f" (on {count} asset(s))" if count else " (unused)"))
        return self._tag_reply(done, problems)

    def _create_category(self, session: Session, raw: dict):
        name = _clean(raw.get("name"))
        if not name:
            return _fail("give it a name.")
        name = " ".join(name.split())
        if len(name) > CATEGORY_MAX:
            return _fail(f"the name is too long ({len(name)} of {CATEGORY_MAX} characters).")
        ref = (_clean(raw.get("ref")) or name).lower()
        parent = None
        if raw.get("parent") is not None:
            parent = self._category(session, raw.get("parent"))
            if parent is None:
                return _fail("its parent is not a category id from list_tags or a ref from this turn.")
        if ref in self._category_refs:
            held = self._category_refs[ref]
            return f"{held['name']} is already proposed"

        # One that exists already under the same parent is that one, not a twin of it.
        if parent is None or parent[0] == "id":
            parent_id = parent[1] if parent else None
            twin = session.exec(select(TagCategory).where(
                TagCategory.user_id == self.user_id,
                TagCategory.parent_category_id == parent_id)).all()
            match = next((c for c in twin if c.name.lower() == name.lower()), None)
            if match is not None:
                self._category_refs[ref] = {"existing_id": match.id, "name": match.name}
                return f"{match.name} exists already ({match.id}), so that is used"
        if self._full():
            return _fail(self._full_sentence())
        op = self._add_op({
            "kind": "create_category", "name": name,
            "parent_category_id": parent[1] if parent and parent[0] == "id" else None,
            "parent_ref": parent[1] if parent and parent[0] == "ref" else None,
            "parent_label": self._category_label(session, parent),
        })
        self._category_refs[ref] = op
        return f"new category {self._category_label(session, ('ref', op['op_ref']))}"

    def _move_category(self, session: Session, raw: dict):
        category = self._owned_category(session, raw.get("category_id"))
        if category is None:
            return _fail("no such category. Use an id from list_tags.")
        new_name = _clean(raw.get("new_name"))
        new_name = " ".join(new_name.split()) if new_name else None
        if new_name and len(new_name) > CATEGORY_MAX:
            return _fail(f"the name is too long ({len(new_name)} of {CATEGORY_MAX} characters).")
        moving = "parent" in raw
        parent = None
        if moving and raw.get("parent") is not None:
            parent = self._category(session, raw.get("parent"))
            if parent is None:
                return _fail("its new parent is not a category id from list_tags or a ref from this turn.")
            if self._would_loop(session, category.id, parent):
                return _fail(f"{category.name} cannot go inside itself or one of its own subcategories.")
        if not moving and not new_name:
            return _fail("give a new parent or a new name.")
        if moving and parent is None and category.parent_category_id is None and not new_name:
            return f"{category.name} is already at the top level"
        if self._full():
            return _fail(self._full_sentence())
        paths = tag_service.category_paths(
            session.exec(select(TagCategory).where(TagCategory.user_id == self.user_id)).all())
        self.tag_ops = [op for op in self.tag_ops
                        if not (op["kind"] == "move_category" and op["category_id"] == category.id)]
        op = self._add_op({
            "kind": "move_category", "category_id": category.id, "name": category.name,
            "from_path": paths.get(category.id, category.name), "move": moving,
            "parent_category_id": parent[1] if parent and parent[0] == "id" else None,
            "parent_ref": parent[1] if parent and parent[0] == "ref" else None,
            "parent_label": self._category_label(session, parent) if moving else None,
            "new_name": new_name,
        })
        where = f" under {op['parent_label']}" if parent else (" to the top level" if moving else "")
        return f"move {category.name}{where}" + (f", renamed {new_name}" if new_name else "")

    def _file_tags(self, session: Session, raw: dict):
        ids = self._ids(raw.get("tag_ids"))
        if not ids:
            return _fail("give tag_ids from list_tags.")
        if "category" not in raw:
            return _fail("give a category, or null to take the tags out of any.")
        target = None
        if raw.get("category") is not None:
            target = self._category(session, raw.get("category"))
            if target is None:
                return _fail("not a category id from list_tags or a ref from this turn.")
        tags = [t for t in (self._owned_tag(session, i) for i in ids) if t is not None]
        unknown = len(ids) - len(tags)
        if not tags:
            return _fail("none of those are tags. Use ids from list_tags.")
        # Already where they are going: nothing to propose for those.
        home = target[1] if target and target[0] == "id" else None
        if target is None or target[0] == "id":
            settled = [t for t in tags if t.category_id == home]
            tags = [t for t in tags if t.category_id != home]
            if not tags:
                return f"{len(settled)} tag(s) are already filed there"
        key = (target[0], target[1]) if target else None
        # A tag filed again moves to the latest home, and is taken out of the earlier one.
        moving = {t.id for t in tags}
        for op in [o for o in self.tag_ops if o["kind"] == "file_tags"]:
            if self._file_key(op) == key:
                continue
            op["tags"] = [t for t in op["tags"] if t["id"] not in moving]
            if not op["tags"]:
                self.tag_ops.remove(op)
            else:
                op["asset_count"], op["sample_assets"] = tag_service.asset_names_for_tags(
                    session, [t["id"] for t in op["tags"]])
        op = next((o for o in self.tag_ops if o["kind"] == "file_tags" and self._file_key(o) == key), None)
        if op is None:
            if self._full():
                return _fail(self._full_sentence())
            op = self._add_op({
                "kind": "file_tags", "tags": [],
                "category_id": target[1] if target and target[0] == "id" else None,
                "category_ref": target[1] if target and target[0] == "ref" else None,
                "category_label": self._category_label(session, target),
            })
        held = {t["id"] for t in op["tags"]}
        op["tags"] += [{"id": t.id, "name": t.name} for t in tags if t.id not in held]
        op["tags"] = op["tags"][:MAX_TAG_IDS]
        op["asset_count"], op["sample_assets"] = tag_service.asset_names_for_tags(
            session, [t["id"] for t in op["tags"]])
        where = op["category_label"] or "no category"
        return (f"file {len(tags)} tag(s) under {where}"
                + (f" ({unknown} id(s) were not tags)" if unknown else ""))

    def _rename(self, session: Session, raw: dict):
        tag = self._owned_tag(session, raw.get("tag_id"))
        if tag is None:
            return _fail("no such tag. Use an id from list_tags.")
        new_name = tag_service.normalise(raw.get("new_name") if isinstance(raw.get("new_name"), str) else "")
        if not new_name:
            return _fail("give the new name.")
        if len(new_name) > TAG_NAME_MAX:
            return _fail(f"the name is too long ({len(new_name)} of {TAG_NAME_MAX} characters).")
        if new_name == tag.name:
            return f"{tag.name} is already called that"
        clash = tag_service.find(session, self.user_id, new_name)
        if clash is not None and clash.id != tag.id:
            return self._merge(session, {"source_tag_ids": [tag.id], "target_tag_id": clash.id})
        if self._claimed(tag.id, "rename_tag"):
            return _fail(f"{tag.name} is already being merged or deleted in this proposal.")
        taken = next((op for op in self.tag_ops if op["kind"] == "rename_tag" and op["tag_id"] != tag.id
                      and op["to"].lower() == new_name.lower()), None)
        if taken is not None:
            return _fail(f"{taken['from']} is already being renamed {taken['to']}; merge them instead.")
        self.tag_ops = [op for op in self.tag_ops
                        if not (op["kind"] == "rename_tag" and op["tag_id"] == tag.id)]
        if self._full():
            return _fail(self._full_sentence())
        count, sample = tag_service.asset_names_for_tags(session, [tag.id])
        self._add_op({"kind": "rename_tag", "tag_id": tag.id, "from": tag.name, "to": new_name,
                      "asset_count": count, "sample_assets": sample})
        return f"rename {tag.name} to {new_name} on {count} asset(s)"

    def _merge(self, session: Session, raw: dict):
        ids = self._ids(raw.get("source_tag_ids"))
        sources = [t for t in (self._owned_tag(session, i) for i in ids) if t is not None]
        target = None
        target_name = None
        if raw.get("target_tag_id"):
            target = self._owned_tag(session, raw.get("target_tag_id"))
            if target is None:
                return _fail("the target is not a tag. Use an id from list_tags, or give target_name.")
        else:
            target_name = tag_service.normalise(
                raw.get("target_name") if isinstance(raw.get("target_name"), str) else "")
            if not target_name:
                return _fail("give target_tag_id or target_name.")
            if len(target_name) > TAG_NAME_MAX:
                return _fail(f"the name is too long ({len(target_name)} of {TAG_NAME_MAX} characters).")
            target = tag_service.find(session, self.user_id, target_name)
        if target is not None:
            sources = [t for t in sources if t.id != target.id]
        if not sources:
            return _fail("give at least one source tag, other than the target, from list_tags.")
        for tag in sources + ([target] if target else []):
            if self._claimed(tag.id, "merge_tags"):
                return _fail(f"{tag.name} is already being renamed, merged or deleted in this proposal.")
        if self._full():
            return _fail(self._full_sentence())
        count, sample = tag_service.asset_names_for_tags(session, [t.id for t in sources])
        into = target.name if target else target_name
        self._add_op({
            "kind": "merge_tags",
            "sources": [{"id": t.id, "name": t.name} for t in sources],
            "target_id": target.id if target else None, "target_name": into,
            "asset_count": count, "sample_assets": sample,
        })
        return (f"merge {', '.join(t.name for t in sources)} into "
                f"{into}{'' if target else ' (a new tag)'} on {count} asset(s)")

    # helpers for the above

    def _owned_tag(self, session: Session, tag_id: object) -> Optional[Tag]:
        tag = session.get(Tag, tag_id) if isinstance(tag_id, str) and tag_id else None
        return tag if tag is not None and tag.user_id == self.user_id else None

    def _owned_category(self, session: Session, category_id: object) -> Optional[TagCategory]:
        row = session.get(TagCategory, category_id) if isinstance(category_id, str) and category_id else None
        return row if row is not None and row.user_id == self.user_id else None

    def _category(self, session: Session, value: object) -> Optional[tuple]:
        """A category named by the model: ("ref", op_ref) for one proposed this turn, or
        ("id", id) for one that exists. A ref is looked up first, since the model is told it
        may call a new category by its name, and an id is never a name it was given."""
        if not isinstance(value, str) or not value.strip():
            return None
        held = self._category_refs.get(value.strip().lower())
        if held is not None:
            return ("id", held["existing_id"]) if "existing_id" in held else ("ref", held["op_ref"])
        row = self._owned_category(session, value.strip())
        if row is not None:
            return ("id", row.id)
        # Models name a category by what they read ("People", "People > Scientists") as
        # often as by its id. Taken when it picks out exactly one; two "Other"s under
        # different parents is a question for the model, not a guess for this tool.
        wanted = " > ".join(part.strip() for part in value.split(">")).lower()
        paths = tag_service.category_paths(session.exec(
            select(TagCategory).where(TagCategory.user_id == self.user_id)).all())
        hits = [cid for cid, path in paths.items()
                if path.lower() == wanted or path.lower().split(" > ")[-1] == wanted]
        exact = [cid for cid in hits if paths[cid].lower() == wanted]
        chosen = exact if len(exact) == 1 else hits
        return ("id", chosen[0]) if len(chosen) == 1 else None

    def _category_label(self, session: Session, which: Optional[tuple]) -> Optional[str]:
        """The path a person reads, "People > Scientists", for an existing or proposed one."""
        if which is None:
            return None
        names, current = [], which
        for _ in range(TREE_DEPTH):
            if current is None:
                break
            if current[0] == "ref":
                op = next((o for o in self.tag_ops if o["op_ref"] == current[1]), None)
                if op is None:
                    break
                names.append(op["name"])
                current = (("ref", op["parent_ref"]) if op["parent_ref"]
                           else ("id", op["parent_category_id"]) if op["parent_category_id"] else None)
            else:
                row = self._owned_category(session, current[1])
                if row is None:
                    break
                names.append(row.name)
                current = ("id", row.parent_category_id) if row.parent_category_id else None
        return " > ".join(reversed(names))

    def _would_loop(self, session: Session, category_id: str, parent: tuple) -> bool:
        """True if putting the category under this parent makes it its own ancestor, taking
        account of the moves and new categories already proposed this turn."""
        moved = {op["category_id"]: op for op in self.tag_ops
                 if op["kind"] == "move_category" and op["move"] and op["category_id"] != category_id}
        current: Optional[tuple] = parent
        for _ in range(TREE_DEPTH):
            if current is None:
                return False
            kind, value = current
            if kind == "id" and value == category_id:
                return True
            op = (next((o for o in self.tag_ops if o["op_ref"] == value), None) if kind == "ref"
                  else moved.get(value))
            if op is not None:
                current = (("ref", op["parent_ref"]) if op["parent_ref"]
                           else ("id", op["parent_category_id"]) if op["parent_category_id"] else None)
                continue
            row = self._owned_category(session, value) if kind == "id" else None
            if row is None:
                return False
            current = ("id", row.parent_category_id) if row.parent_category_id else None
        return True

    @staticmethod
    def _file_key(op: dict) -> Optional[tuple]:
        if op["category_ref"]:
            return ("ref", op["category_ref"])
        return ("id", op["category_id"]) if op["category_id"] else None

    def _claimed(self, tag_id: str, kind: str) -> bool:
        """Whether another rename, merge or delete in this proposal already names the tag.
        One change to a tag's identity per proposal: two would have to apply in an order
        the person never sees, and the second would find the tag gone."""
        for op in self.tag_ops:
            if op["kind"] == "rename_tag" and op["tag_id"] == tag_id and kind != "rename_tag":
                return True
            if op["kind"] == "delete_tag" and op["tag_id"] == tag_id and kind != "delete_tag":
                return True
            if op["kind"] == "merge_tags" and (op["target_id"] == tag_id
                                               or any(t["id"] == tag_id for t in op["sources"])):
                return True
        return False

    def _full(self) -> bool:
        return len(self.tag_ops) >= MAX_TAG_OPS

    @staticmethod
    def _full_sentence() -> str:
        return (f"that is {MAX_TAG_OPS} tag changes already, the most one request may "
                "propose. Say what is left and let the person ask for more.")

    def _add_op(self, op: dict) -> dict:
        op = {"op_ref": str(uuid.uuid4()), **op}
        self.tag_ops.append(op)
        return op

    @staticmethod
    def _ids(value: object) -> List[str]:
        seen: List[str] = []
        for raw in value if isinstance(value, list) else []:
            if isinstance(raw, str) and raw.strip() and raw.strip() not in seen:
                seen.append(raw.strip())
        return seen[:MAX_TAG_IDS]

    def _tag_reply(self, done: List[str], problems: List[str]) -> object:
        if not done:
            return _fail("Nothing was recorded. " + " ".join(problems))
        self._step("propose", f"Proposing {len(done)} change(s) to the tags")
        return ("Recorded: " + "; ".join(done) + "."
                + (" Not recorded: " + " ".join(problems) if problems else ""))

    def _playlist_entry(self, existing, name: Optional[str], description: Optional[str]) -> dict:
        for held in self.playlists:
            if existing is not None and held["playlist_id"] == existing.id:
                return held
            if existing is None and held["playlist_id"] is None and name \
                    and held["name"].lower() == name.lower():
                if description and not held.get("description"):
                    held["description"] = description
                return held
        return {
            "playlist_ref": str(uuid.uuid4()),
            "playlist_id": existing.id if existing is not None else None,
            "name": existing.name if existing is not None else name,
            "description": None if existing is not None else description,
            "items": [],
        }

    @staticmethod
    def _already_listed(entry: dict, resolved) -> bool:
        for held in entry["items"]:
            if held["asset_id"] != resolved.asset.id:
                continue
            if held["in_point"] is None or resolved.in_point is None:
                if held["in_point"] is None and resolved.in_point is None:
                    return True
                continue
            if (abs(held["in_point"] - resolved.in_point) < SAME_RANGE_SECONDS
                    and abs(held["out_point"] - resolved.out_point) < SAME_RANGE_SECONDS):
                return True
        return False

    # ─── the result ──────────────────────────────────────────────────────────

    def _entry(self, asset: Asset, tags: List[str]) -> dict:
        existing = self.items.get(asset.id)
        if existing:
            return dict(existing)
        return {
            "asset_id": asset.id, "asset_name": asset.name,
            # What it is now, so the card can show a change and not only the new value.
            "before": {"name": asset.name, "description": asset.description,
                       "summary": asset.summary, "tags": tags},
            "add_tags": [], "remove_tags": [], "enrich": [],
        }

    @staticmethod
    def _tag_names(value: object) -> List[str]:
        seen, names = set(), []
        for raw in value if isinstance(value, list) else []:
            name = tag_service.normalise(raw) if isinstance(raw, str) else ""
            if name and name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
        return names

    @staticmethod
    def _describe(entry: dict) -> str:
        parts = [f for f in ("name", "description", "summary") if f in entry]
        if entry["add_tags"]:
            parts.append("+tags " + ", ".join(entry["add_tags"]))
        if entry["remove_tags"]:
            parts.append("-tags " + ", ".join(entry["remove_tags"]))
        return f"{entry['asset_name']}: " + "; ".join(parts) + "."

    def proposal(self) -> Optional[dict]:
        items = [i for i in self.items.values()
                 if any(i.get(f) for f in ("name", "description", "summary", "add_tags",
                                           "remove_tags", "enrich"))]
        if not (items or self.clips or self.grabs or self.playlists or self.notes or self.tag_ops):
            return None
        return {"items": items, "clips": list(self.clips), "grabs": list(self.grabs),
                "playlists": list(self.playlists),
                "notes": list(self.notes), "tag_ops": list(self.tag_ops)}

    def proposal_json(self) -> Optional[str]:
        proposal = self.proposal()
        return json.dumps(proposal, ensure_ascii=False) if proposal else None
