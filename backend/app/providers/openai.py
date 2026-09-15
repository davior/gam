"""The OpenAI chat-completions protocol.

Serves `openai`, `custom`, and `deepseek` when it has not been pointed at the
Anthropic-compatible endpoint. Named for the protocol rather than the vendor, because
three of the five provider types can speak it.

Not to be confused with `app.embeddings.openai`, which is the same vendor and a
different capability — see docs/m6-ai-enrichment.md on why those are two settings.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Sequence

from app.providers import _upstream
from app.providers.base import Completion, Image, ProviderError, Usage
from app.providers.endpoints import openai_compat_base
from app.providers.params import parse_extra_params

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 180.0


class OpenAIProvider:
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
                "type": "image_url",
                "image_url": {
                    # A data: URL, not a link. The picture is in this app's storage and
                    # the provider has no way to fetch it — and giving it a reachable
                    # URL would mean making the asset public to do so.
                    "url": f"data:{image.media_type};base64,"
                    + base64.b64encode(image.data).decode("ascii")
                },
            }
            for image in images
        ]
        content.append({"type": "text", "text": prompt})

        messages: list[dict[str, Any]] = []
        if system:
            # A message, not a top-level field: this protocol has no `system` key.
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._provider.max_tokens,
            "messages": messages,
        }
        body.update(parse_extra_params(self._provider.extra_params))

        base = openai_compat_base(self.provider_type, self._provider.base_url)
        response = _upstream.post_json(
            f"{base}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
            },
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
    if not isinstance(payload, dict):
        raise ProviderError("The provider returned something that was not a completion")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("The provider returned no choices")

    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    text = message.get("content")
    text = text.strip() if isinstance(text, str) else ""

    if not text:
        if first.get("finish_reason") == "length":
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

    input_tokens = count("prompt_tokens")
    output_tokens = count("completion_tokens")
    if not (input_tokens or output_tokens):
        # Several OpenAI-compatible servers report only a total. Counting it as input
        # is the conservative reading: input is the cheaper side, so this understates
        # rather than invents, and step 3 will mark any figure derived from it estimated.
        input_tokens = count("total_tokens")

    return Completion(
        text=text,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        model=model,
        provider_type=provider_type,
    )
