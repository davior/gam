# PDF page grabs

A PDF is a `document` asset: it cannot go in a playlist and Video Creator cannot overlay
it. A page rendered to a picture is an `image`, which both already handle.

## What exists

- `POST /api/assets/{pdf_id}/grab-page` with `{"page": 3, "scale": 2.0?, "name": "…"?}`
  renders the page (pypdfium2, PNG, longest side capped at 4096px) and files it as an image.
  Resolves at once with the new asset (201); a page renders in well under a second, so there
  is no job. Errors are `cannot_grab_page` (400, with a sentence: not a PDF, past the end,
  cannot be opened) or `not_found`.
- `GET /api/assets/{pdf_id}/pages` gives `{"page_count": N}` for a page picker.
- The picture is `source = "pdf_page"`, `parent_asset_id` = the PDF, `source_page` = the
  page. It inherits the PDF's attribution through the parent, survives the PDF being deleted
  (the parent link is cleared), and is left out of the PDF's Clips list.
- The assistant finds the page (`search_assets` names a document hit's page; `find_pages`
  searches inside one PDF; `read_page` checks it) and proposes it with `propose_grab`.
  Nothing is made until Apply.
- The PDF's Text tab has a page picker and a "Grab as image" button per page.

## Not yet

- **Cropping to a region** (the table, not the page). `pypdfium2`'s `render(crop=…)` can do
  it; what is missing is something to say where the table is (a vision model proposing a
  box) and a crop UI over a page preview.
- **Scanned PDFs** have no extracted text, so `find_pages` cannot search them. A page can
  still be grabbed by number.

## For Video Creator

GVC can call the endpoint as the caller, the way it calls `/api/gam/import-image`, then use
the returned asset id as an ordinary image asset (`ImageOverlay` or a scene visual). That is
a change in GVC, not here.
