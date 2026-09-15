"""Ollama's own chat protocol — the provider that needs no key and sends nothing away.

For a library of private material that is the whole argument: every other option means
shipping transcripts and stills to a third party to have them described.

`/api/chat` with `stream: false`. Ollama nests sampling parameters under `options` and
calls the output cap `num_predict`, so this is the one client where `max_tokens` and
`extra_params` do not go at the top level.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Sequence

from app.providers import _upstream
from app.providers.base import Completion, Image, ProviderError, Usage
from app.providers.endpoints import OLLAMA_BASE
from app.providers.params import parse_extra_params

logger = logging.getLogger(__name__)

# Local inference on CPU is slow, and unlike the hosted providers there is no queue in
# front of it to fail fast — it is simply working. Matches the Ollama embedder.
REQUEST_TIMEOUT = 600.0


class OllamaProvider:
    def __init__(self, provider):
        # No key parameter, unlike the other two: Ollama has no credential at all, and
        # a constructor that accepted one would suggest otherwise.
        self._provider = provider

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

        message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            # A flat list of base64 strings on the message, with no media type: Ollama
            # sniffs the format itself, and there is nowhere in its schema to tell it.
            message["images"] = [
                base64.b64encode(image.data).decode("ascii") for image in images
            ]

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(message)

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        options: dict[str, Any] = dict(parse_extra_params(self._provider.extra_params))
        options["num_predict"] = self._provider.max_tokens
        body["options"] = options

        base = (self._provider.base_url or OLLAMA_BASE).rstrip("/")
        response = _upstream.post_json(
            f"{base}/api/chat",
            headers={"content-type": "application/json"},
            json_body=body,
            timeout=REQUEST_TIMEOUT,
            label=self._provider.name,
        )

        if response.status_code == 404:
            # By far the most common misconfiguration, and the message says the fix —
            # the same courtesy the Ollama embedder already extends.
            raise ProviderError(
                f"Ollama has no model '{self.model}'. Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"{self._provider.name}: {_upstream.error_detail(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Ollama returned a response that was not JSON") from exc

        return parse_response(payload, model=self.model, provider_type=self.provider_type)


def parse_response(payload: Any, *, model: str, provider_type: str) -> Completion:
    if not isinstance(payload, dict):
        raise ProviderError("Ollama returned something that was not a message")

    message = payload.get("message")
    message = message if isinstance(message, dict) else {}
    text = message.get("content")
    text = text.strip() if isinstance(text, str) else ""

    if not text:
        raise ProviderError("Ollama returned an empty reply")

    def count(key: str) -> int:
        try:
            return int(payload.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return Completion(
        text=text,
        # Ollama's own names for the two sides. Recorded for symmetry rather than for
        # billing: local inference costs nothing, which is the point of choosing it.
        usage=Usage(input_tokens=count("prompt_eval_count"), output_tokens=count("eval_count")),
        model=model,
        provider_type=provider_type,
    )
