"""The Anthropic Messages protocol.

Serves `anthropic`, and also `deepseek` or a `custom` gateway when `use_anthropic_api`
is set — which is not a cosmetic option: DeepSeek's Anthropic-compatible endpoint runs
the same server-side web_search tool Claude does, and its OpenAI-compatible one has no
such tool. See `app.providers.endpoints`.

The request body is built here, on the server. gecko-notes assembles it in the browser
and ships it with the job, which GAM cannot reuse — an enrichment job runs on a worker
thread with no browser anywhere near it.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Sequence

from app.providers import _upstream
from app.providers.base import Completion, Image, ProviderError, Usage
from app.providers.endpoints import anthropic_base, anthropic_headers
from app.providers.params import parse_extra_params

logger = logging.getLogger(__name__)

# Generation is slow and this is a blocking call, so the read window has to cover the
# whole answer. _upstream keeps connect/write short, so an unreachable endpoint still
# fails fast rather than sitting here for three minutes.
REQUEST_TIMEOUT = 180.0


class AnthropicProvider:
    def __init__(self, provider, api_key: str):
        self._provider = provider
        self._api_key = api_key

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def provider_type(self) -> str:
        return self._provider.provider_type

    @property
    def supports_images(self) -> bool:
        return bool(self._provider.supports_images)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: Sequence[Image] = (),
    ) -> Completion:
        if images and not self.supports_images:
            raise ProviderError(
                f"{self._provider.name} is not configured to accept images. "
                "Tick 'This model can look at images' in Settings if the model supports them."
            )

        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": base64.b64encode(image.data).decode("ascii"),
                },
            }
            for image in images
        ]
        # Text last. A vision model answers about the pictures it has already been
        # shown, and the ordering is what every Anthropic example uses.
        content.append({"type": "text", "text": prompt})

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._provider.max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            body["system"] = system
        body.update(parse_extra_params(self._provider.extra_params))

        response = _upstream.post_json(
            f"{anthropic_base(self._provider)}/v1/messages",
            headers=anthropic_headers(self._api_key, self.provider_type),
            json_body=body,
            timeout=REQUEST_TIMEOUT,
            label=self._provider.name,
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"{self._provider.name}: {_upstream.error_detail(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self._provider.name} returned a response that was not JSON") from exc

        return parse_response(payload, model=self.model, provider_type=self.provider_type)


def parse_response(payload: Any, *, model: str, provider_type: str) -> Completion:
    """Pull the text and the token counts out of a Messages response.

    Split from the request so it can be tested against a recorded body with no network,
    which is how `enrichment/deepgram.py` is structured and for the same reason.
    """
    if not isinstance(payload, dict):
        raise ProviderError("The provider returned something that was not a message")

    blocks = payload.get("content")
    if not isinstance(blocks, list):
        raise ProviderError("The provider returned a message with no content")

    # Concatenate every text block. A response that used a tool has non-text blocks in
    # the same array, and picking content[0] would return an empty string for one of
    # them — which reads as "the model had nothing to say" rather than as a bug.
    text = "".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()

    if not text:
        stop = payload.get("stop_reason")
        if stop == "max_tokens":
            raise ProviderError(
                "The reply was cut off before any text arrived — raise the maximum "
                "response length for this provider in Settings."
            )
        raise ProviderError("The provider returned an empty reply")

    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}

    def count(key: str) -> int:
        try:
            return int(usage.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    # Cache reads and writes are billable input, so they belong on the input side rather
    # than being dropped — a cached prompt that reports input_tokens=12 has not become
    # free, it has become cheaper.
    input_tokens = count("input_tokens") + count("cache_read_input_tokens") + count(
        "cache_creation_input_tokens"
    )

    return Completion(
        text=text,
        usage=Usage(input_tokens=input_tokens, output_tokens=count("output_tokens")),
        model=model,
        provider_type=provider_type,
    )
