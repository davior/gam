"""Background work.

`runner` is the generic machinery — a queue, worker threads, heartbeats, cancellation
and restart recovery. `registry` is the union every job kind is read and cancelled
through. `enrichment` is the one kind that exists so far.
"""
