# Playlists

A playlist is a named, ordered run of videos, audio, images and moments of them, played
one after another. It can be built by hand (the Playlists page, the library's selection
bar, an asset's Details tab) or by the assistant ("make a playlist of people talking about
the financial crisis").

## Decisions

- **An item is an asset plus an optional range, not a clip.** The moments live in the
  playlist: a playlist of twenty moments from ten interviews leaves no clips behind in the
  library, and each range stays editable from the playlist page. (Asked and settled with
  the owner; see the "Real clips" alternative below for what was turned down.)
- **An item always names a file-owning asset.** A live clip added to a playlist is stored
  as its original plus the clip's window (`services/playlists.resolve`). Video Creator
  cuts from files, and a range on a clip would be a range on a range.
- **Only what plays.** Video, audio and images. A document is refused (`not_playable`).
- **A range is held to the clip rules** (`services/assets.cut_problem`): inside the file,
  out after in. An image has no range; it has `image_seconds` (default 5).
- **Deleting an asset removes its items** (`assets._delete_dependents`); the playlist
  keeps the rest. Deleting a playlist never touches the assets.
- **Ranges are enforced in the player**, client-side, as a clip's are on the asset page:
  `/media` serves whole files.

Rejected: making each AI-found moment a live clip in the library. It reused the clip code,
but a big playlist would have filled the library and every video's Clip tab with rows the
person never asked for.

## Tables

`playlist` (`id`, `user_id`, `name`, `description`, timestamps) and `playlist_item`
(`id`, `playlist_id`, `asset_id`, `position`, `in_point`, `out_point`, `image_seconds`,
`label`, `note`, timestamps). Migration `5f2b8c1d9e73`. Both foreign keys are real: an
item cannot outlive its asset or its playlist.

## API (`/api/playlists`)

| Method | Path | |
|---|---|---|
| GET | `/` | List: `item_count`, `total_seconds`, the first item's `thumb_url` |
| POST | `/` | `{name, description?, items?: [ItemIn]}` |
| GET | `/{id}` | The playlist and its items, in order |
| PATCH | `/{id}` | `{name?, description?}` |
| DELETE | `/{id}` | |
| POST | `/{id}/items` | `{items: [ItemIn], position?}`: all or none |
| PATCH | `/{id}/items/{item_id}` | Only the fields sent. A range is a pair; both `null` = the whole asset |
| DELETE | `/{id}/items/{item_id}` | |
| PUT | `/{id}/order` | `{item_ids}`: every current id exactly once, else `409 stale_order` |

`ItemIn = {asset_id, in_point?, out_point?, image_seconds?, label?, note?}`.

An item reads back as:

```json
{
  "id": "…", "position": 0,
  "asset_id": "…", "asset_name": "Interview", "asset_type": "video",
  "in_point": 62.4, "out_point": 101.0,
  "start": 62.4, "end": 101.0,
  "image_seconds": null, "label": "On the bailout", "note": "Explains the bailout.",
  "asset_duration_seconds": 1800.0, "mime_type": "video/mp4", "width": 1920, "height": 1080,
  "file_url": "/media/…?exp=…&sig=…", "thumb_url": "/media/…", "missing": false
}
```

`in_point`/`out_point` are what is stored; `start`/`end` are resolved against the asset
(the whole file when there is no range; `end` is null only if the file was never
measured). URLs are signed and expire (`MEDIA_URL_TTL_SECONDS`): read the playlist again
for fresh ones, which the player does once per item when a file will not load.

## The assistant

`list_playlists` (looks) and `propose_playlist` (proposes) in `assistant/tools.py`.
`propose_playlist` takes `{name | playlist_id, description?, items: [{asset_id,
start_seconds?, end_seconds?, label?, reason?}]}`; a second call with the same name or
`playlist_id` adds to the same proposed playlist. Items are checked one by one and the
good ones kept, so the model hears which range was wrong. At most 60 items per turn.

The proposal gains `"playlists": [{playlist_ref, playlist_id, name, description, items}]`.
Apply (`assistant/apply._apply_playlist`) checks every item again, leaves out and reports
any whose asset has gone, and makes or appends through `services/playlists`. The Apply
body takes `playlist_refs` beside `asset_ids` and `clip_ids`.

## What Video Creator reads

A GVC spec already refers to GAM media as `{from: "gam", id, in, out}`
(gvc `backend/app/spec/model.py`). A playlist item maps onto that with nothing to
translate: `id = asset_id`, `in = in_point`, `out = out_point` (both absent for a whole
asset). `GET /api/playlists` and `GET /api/playlists/{id}`, called with the person's own
token as GVC already calls GAM's search and assets, are all it needs.

**Proposed change in GVC** (to be specified in its plan, not applied from here):

1. `GamClient.list_playlists()` / `get_playlist(id)`, and `GET /api/gam/playlists[/{id}]`
   through GVC's backend, as its other GAM reads go.
2. "New project from a playlist": one scene per item, in order, each with its media as
   the item's GAM ref and its duration `end - start` (an image's `image_seconds`), the
   item's `label` as the scene's working title.
3. The same list in GVC's GAM picker, so a playlist can be dropped into an existing
   project's timeline.

Nothing about this needs GAM to change further: the playlist is the contract.
