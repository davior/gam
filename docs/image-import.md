# Import a picture by its address

**Status:** built. For Gecko Video Creator's image search: GVC finds pictures through
Gecko Suite (`POST /api/v1/search/images`, the suite's configured web search backend),
the person picks one for a background or an overlay, and GVC files it here so the video
refers to a GAM asset like any other.

`POST /api/assets/import-image`

```json
{"image_url": "https://…/owl.jpg", "page_url": "https://…/owls", "title": "…",
 "description": "…", "tags": ["…"]}
```

Answers `201` with the new asset, or `200` with the one already here. Errors are
`400 image_unavailable` (the sentence says why: not a picture, too large, refused by the
site, timed out) or `400 unsafe_url`.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Queued or inline | **Inline** | The caller is waiting to place the picture. One bounded download (40 MB at most) and a thumbnail would spend longer queued than fetching. Every other import is a video or a track: minutes, so a job. |
| What is stored | JPEG, PNG, GIF, WebP, AVIF, BMP, TIFF | Narrower than an upload. **No SVG** (a document that can carry script, served back from this origin), no HEIC or icons. |
| File type | **The server's `Content-Type` first**, the path's extension as the fallback | Picture URLs are full of `.php?id=`, and CDNs send WebP from a `.jpg` path. A page answering for a picture (a hotlink guard, a login wall) is refused rather than stored. |
| Duplicates | Address, then checksum | The image address catches a repeat before downloading when no page was recorded; the checksum catches the rest (CDN size variants, mirrors, a picture uploaded by hand). A duplicate returns the existing asset untouched, so a person's edits stay. |
| Credit | `source_url` = the page when known, else the picture's address. `retrieved_at` = now. The search's description as `description`. All stamped `embedded` | The page is what a citation names. **Licence and creator are left empty**: no search backend knows them, and an empty licence is the "unknown" `unattributed=true` already finds. |
| Safety | The SSRF check on every redirect hop, uncompressed bodies only, capped while it arrives | `ingest/web.py`'s rules, shared with the track import (`download_image` beside `download_audio`). |

## Files

| File | Role |
|---|---|
| `backend/app/routers/imports.py` | The route. |
| `backend/app/services/image_import.py` | Duplicate checks, download, `ingest_file`, the source fields. |
| `backend/app/ingest/web.py` | `download_image`, and the type rules. |
| `backend/tests/test_image_import.py` | Through the route, with the network faked at `web`'s client. |
