"""AI enrichment of stored assets.

Transcription lands here first because it is what M5's search needs: you cannot find
the moment somebody said something until the words exist. Description, summarisation,
tagging and embedding follow in later milestones and share this package's shape — a
pure function that takes an asset and a progress callback, with the job plumbing kept
in app/jobs.
"""
