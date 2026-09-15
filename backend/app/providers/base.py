"""What a generative provider has to do.

Deliberately small, like `app.embeddings.base`. A provider takes a prompt and returns
text plus what it cost in tokens; prompt construction, retries over *content*, and what
to do with the answer belong to the caller, so adding a provider never means touching a
job.

One turn, not a conversation. gecko-notes models this as a `messages` list because it is
backing a chat in a browser; GAM's callers are `describe`, `summarize` and `autotag`,
each of which asks exactly one question and reads exactly one answer. A message list
here would be an abstraction with no second user.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class ProviderError(Exception):
    """The provider refused, failed, or returned something unusable."""


class ProviderUnavailable(ProviderError):
    """No usable provider is configured for this user.

    Separate from ProviderError because it is not a failure — it is a library that has
    not been set up yet, and the answer is a link to Settings rather than a retry.
    """


@dataclass(frozen=True)
class Image:
    """One picture to show the model, as bytes.

    Kept as raw bytes rather than base64: each protocol wants it encoded differently —
    Anthropic in a source block, OpenAI in a data: URL, Ollama as a bare string in a
    list — and encoding once at the call site would mean decoding it again for two of
    the three.
    """

    data: bytes
    media_type: str


@dataclass(frozen=True)
class Usage:
    """What one call consumed.

    Returned rather than recorded. Persisting it is `UsageEvent`, which does not exist
    yet; when it does, the recording belongs at the job boundary where the user and the
    asset are both in scope, not inside an HTTP client.
    """

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    model: str
    # The provider's own type, not the protocol it spoke. A DeepSeek provider addressed
    # over the Anthropic Messages endpoint is still DeepSeek spend, and costing it as
    # Anthropic would be wrong by an order of magnitude.
    provider_type: str


class LLMProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def provider_type(self) -> str: ...

    @property
    def supports_images(self) -> bool:
        """Whether `complete` will accept images.

        A text-only backend does not refuse a picture cleanly — it fails somewhere
        inside its own deserializer, and the message that comes back describes a JSON
        shape rather than the actual problem. Callers check this first.
        """
        ...

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: Sequence[Image] = (),
    ) -> Completion:
        """One question, one answer.

        Raises ProviderError for anything that did not produce usable text, including
        being handed images when `supports_images` is False.
        """
        ...
