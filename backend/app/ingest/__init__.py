"""Turning an uploaded file into a catalogued asset.

`filetypes` decides what a file is, `probe` reads what the file says about itself, and
`thumbnails` makes it recognisable in a grid. None of them raise on a file they cannot
handle: ingestion promises that a dropped file is kept, so enrichment that fails leaves
the asset poorer, never rejected.
"""
