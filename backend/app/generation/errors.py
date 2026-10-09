"""The one exception a generation fails with."""


class GenerationError(Exception):
    """Something the user can be told about, in a sentence they can act on.

    Its own class rather than a ProviderError so the job runner can catch it by name and
    record the message without a traceback: a rejected key or a refused prompt is an
    answer, not a crash.
    """


class FalUnavailable(GenerationError):
    """fal.ai answered 429 or 5xx — worth asking again rather than giving up on.

    Separate because the poll loop treats it so: a video four minutes into generating
    should not be abandoned, and billed for nothing, over one bad gateway on a status
    check.
    """
