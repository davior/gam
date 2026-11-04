"""The assistant's tools: three that look, one that lists, two that propose.

None of them writes to the library. `propose_changes` and `propose_enrichment` add to a
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
from contextlib import contextmanager
from typing import Callable, ContextManager, Dict, Iterator, List, Optional

from gecko_suite_client import ToolSpec
from sqlmodel import Session, col, select

from app.config import settings
from app.enrichment.describe import describable
from app.enrichment.source import document_text
from app.enrichment.summarize import summarisable
from app.models.asset import Asset
from app.models.tag import Tag
from app.models.transcript import TranscriptSegment
from app.services import search as search_service
from app.services import tags as tag_service

# What a proposal may ask the enrichment pipeline to do once applied.
ENRICHMENTS = ("summarize", "describe", "autotag")

MAX_RESULTS = 20
NAME_MAX, TEXT_MAX = 255, 20_000

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
        "The words spoken in a video or audio asset (or the text of a document), with "
        "timestamps. Long ones come in parts: pass start_seconds to carry on.",
        {"type": "object", "properties": {
            "asset_id": {"type": "string"},
            "start_seconds": {"type": "number", "description": "Where to start. Default 0."},
        }, "required": ["asset_id"]}),
    ToolSpec(
        "list_tags",
        "The person's existing tags, most used first. Look here before proposing a tag.",
        {"type": "object", "properties": {}}),
    ToolSpec(
        "web_search",
        "Search the web, for what the library cannot say: who someone is, when something "
        "happened, what a term means. Returns titles, links and short passages. Use it to "
        "inform a proposal or an answer, and say where a fact came from; it is not a way to "
        "find the person's own assets (use search_assets).",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
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
]

# Tools that only look. The worker reports a step for the rest.
READS = frozenset({"search_assets", "get_asset", "read_transcript", "list_tags", "web_search"})

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


class Working:
    """One turn's tools and the proposal they build."""

    def __init__(self, user_id: str, open_session: Callable[[], ContextManager[Session]],
                 on_step: Callable[[str, str], None] = lambda kind, text: None):
        self.user_id = user_id
        self._open = open_session
        self._step = on_step
        # asset_id -> the proposal's entry for it, in the order they were first proposed.
        self.items: Dict[str, dict] = {}
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
            "list_tags": self.list_tags,
            "web_search": self.web_search,
            "propose_changes": self.propose_changes,
            "propose_enrichment": self.propose_enrichment,
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
                ("creator", asset.creator), ("publisher", asset.publisher),
                ("source title", asset.source_title), ("published", asset.published_date),
                ("source url", asset.source_url), ("license", asset.license),
            ]
            return "\n".join(f"{label}: {value}" for label, value in fields if value)

    def read_transcript(self, args: dict) -> object:
        with self._session() as session:
            asset = self._owned(session, args.get("asset_id"))
            if asset is None:
                return _fail("No such asset. Use an id from search_assets.")
            self._step("read", f"Reading the transcript of {asset.name}")
            start = args.get("start_seconds")
            start = float(start) if isinstance(start, (int, float)) and start > 0 else 0.0
            segments = session.exec(
                select(TranscriptSegment)
                .where(TranscriptSegment.asset_id == asset.id,
                       TranscriptSegment.end_time >= start)
                .order_by(col(TranscriptSegment.idx))).all()
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
                line = f"[{_clock(segment.start_time)}] {text}"
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

    def list_tags(self, args: dict) -> object:
        with self._session() as session:
            counts = tag_service.usage_counts(session, self.user_id)
            rows = session.exec(select(Tag).where(Tag.user_id == self.user_id)).all()
        if not rows:
            return "There are no tags yet."
        rows = sorted(rows, key=lambda t: (-counts.get(t.id, 0), t.name.lower()))[:200]
        return "\n".join(f"{t.name} ({counts.get(t.id, 0)})" for t in rows)

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
        return {"items": items} if items else None

    def proposal_json(self) -> Optional[str]:
        proposal = self.proposal()
        return json.dumps(proposal, ensure_ascii=False) if proposal else None
