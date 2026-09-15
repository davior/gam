"""The three protocols, as actual bytes on the wire.

Request shaping and response parsing are tested separately, the way
`enrichment/deepgram.py` is: the parse runs against a recorded body with no network, and
the request is asserted by capturing what the transport was handed. The one case that
matters most is at the bottom of each section — a text-only provider handed an image
must be refused *here*, not by a deserializer three networks away.
"""

import base64

import httpx
import pytest

from app.models.provider import AIProvider
from app.providers import _upstream, anthropic, ollama, openai
from app.providers.base import Image, ProviderError

JPEG = Image(data=b"\xff\xd8\xff-not-really-a-jpeg", media_type="image/jpeg")


def row(**overrides) -> AIProvider:
    fields = {
        "user_id": "u",
        "name": "Claude",
        "provider_type": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "supports_images": True,
    }
    fields.update(overrides)
    return AIProvider(**fields)


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    """Capture the outgoing request and reply with whatever the test scripts.

    Patches `post_json` on the module object, not the name each client imported — the
    clients call `_upstream.post_json(...)` through the module precisely so one patch
    reaches all three. `tests/test_embedding_backfill.py` records what happens when that
    is not true.
    """
    state = {"calls": [], "response": httpx.Response(200, json={})}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        state["calls"].append(
            {"url": url, "headers": headers or {}, "body": json_body, "label": label}
        )
        return state["response"]

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


def reply(state, payload, status=200):
    state["response"] = httpx.Response(status, json=payload)


ANTHROPIC_OK = {
    "content": [{"type": "text", "text": "A gecko on a warm rock."}],
    "usage": {"input_tokens": 120, "output_tokens": 8},
}
OPENAI_OK = {
    "choices": [{"message": {"content": "A gecko on a warm rock."}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 120, "completion_tokens": 8},
}
OLLAMA_OK = {
    "message": {"content": "A gecko on a warm rock."},
    "prompt_eval_count": 120,
    "eval_count": 8,
}


# ─── Anthropic Messages ──────────────────────────────────────────────────────


def test_anthropic_builds_the_request_server_side(upstream):
    reply(upstream, ANTHROPIC_OK)

    result = anthropic.AnthropicProvider(row(), "sk-ant-x").complete(
        "Describe this", system="You are terse."
    )

    call = upstream["calls"][0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-ant-x"
    assert call["body"]["model"] == "claude-sonnet-4-20250514"
    assert call["body"]["max_tokens"] == 4096
    assert call["body"]["system"] == "You are terse."
    assert call["body"]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Describe this"}]}
    ]
    assert result.text == "A gecko on a warm rock."


def test_anthropic_omits_system_when_there_is_none(upstream):
    reply(upstream, ANTHROPIC_OK)

    anthropic.AnthropicProvider(row(), "k").complete("Describe this")

    assert "system" not in upstream["calls"][0]["body"]


def test_anthropic_sends_images_as_base64_source_blocks(upstream):
    reply(upstream, ANTHROPIC_OK)

    anthropic.AnthropicProvider(row(), "k").complete("What is this?", images=[JPEG])

    content = upstream["calls"][0]["body"]["messages"][0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(JPEG.data).decode("ascii"),
        },
    }
    # Text last: the model answers about pictures it has already been shown.
    assert content[-1] == {"type": "text", "text": "What is this?"}


def test_anthropic_merges_extra_params_but_not_over_structure(upstream):
    reply(upstream, ANTHROPIC_OK)

    anthropic.AnthropicProvider(
        row(extra_params='{"temperature": 0, "messages": "hijacked"}'), "k"
    ).complete("Hi")

    body = upstream["calls"][0]["body"]
    assert body["temperature"] == 0
    assert isinstance(body["messages"], list)


def test_a_deepseek_row_on_the_messages_protocol_goes_to_deepseeks_endpoint(upstream):
    """The reason `use_anthropic_api` exists: that endpoint runs a server-side web
    search, and the OpenAI-compatible one does not."""
    reply(upstream, ANTHROPIC_OK)

    anthropic.AnthropicProvider(
        row(provider_type="deepseek", model="deepseek-chat", use_anthropic_api=True), "k"
    ).complete("Hi")

    call = upstream["calls"][0]
    assert call["url"] == "https://api.deepseek.com/anthropic/v1/messages"
    # Both auth headers, because compatible gateways disagree about which to read.
    assert call["headers"]["Authorization"] == "Bearer k"


def test_anthropic_concatenates_every_text_block():
    """A response that used a tool has non-text blocks in the same array, and taking
    content[0] would read as "the model had nothing to say"."""
    result = anthropic.parse_response(
        {
            "content": [
                {"type": "tool_use", "id": "t1", "name": "web_search", "input": {}},
                {"type": "text", "text": "First. "},
                {"type": "text", "text": "Second."},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
        model="m",
        provider_type="anthropic",
    )

    assert result.text == "First. Second."


def test_anthropic_counts_cached_input_as_input():
    """A cached prompt has not become free, it has become cheaper — dropping these
    would report a long prompt as costing twelve tokens."""
    result = anthropic.parse_response(
        {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": 12,
                "cache_read_input_tokens": 4000,
                "cache_creation_input_tokens": 100,
                "output_tokens": 8,
            },
        },
        model="m",
        provider_type="anthropic",
    )

    assert result.usage.input_tokens == 4112
    assert result.usage.output_tokens == 8


def test_a_reply_cut_off_before_any_text_says_what_to_change():
    with pytest.raises(ProviderError, match="maximum response length"):
        anthropic.parse_response(
            {"content": [], "stop_reason": "max_tokens"}, model="m", provider_type="anthropic"
        )


def test_the_provider_type_is_reported_not_the_protocol():
    """A DeepSeek provider on the Anthropic endpoint is still DeepSeek spend, and
    costing it as Anthropic would be wrong by an order of magnitude."""
    result = anthropic.parse_response(ANTHROPIC_OK, model="deepseek-chat", provider_type="deepseek")

    assert result.provider_type == "deepseek"


def test_an_http_error_carries_the_providers_own_message(upstream):
    reply(upstream, {"error": {"message": "credit balance is too low"}}, status=400)

    with pytest.raises(ProviderError, match="credit balance is too low"):
        anthropic.AnthropicProvider(row(), "k").complete("Hi")


# ─── OpenAI-compatible ───────────────────────────────────────────────────────


def test_openai_puts_the_system_prompt_in_the_messages(upstream):
    """This protocol has no top-level `system` key."""
    reply(upstream, OPENAI_OK)

    openai.OpenAIProvider(row(provider_type="openai", model="gpt-4o"), "sk-x").complete(
        "Describe this", system="You are terse."
    )

    call = upstream["calls"][0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-x"
    assert call["body"]["messages"][0] == {"role": "system", "content": "You are terse."}
    assert "system" not in call["body"]


def test_openai_sends_images_as_data_urls(upstream):
    """Not a link: the picture is in this app's storage, and giving the provider a
    reachable URL would mean making the asset public to do it."""
    reply(upstream, OPENAI_OK)

    openai.OpenAIProvider(row(provider_type="openai", model="gpt-4o"), "k").complete(
        "What is this?", images=[JPEG]
    )

    content = upstream["calls"][0]["body"]["messages"][0]["content"]
    expected = "data:image/jpeg;base64," + base64.b64encode(JPEG.data).decode("ascii")
    assert content[0] == {"type": "image_url", "image_url": {"url": expected}}


def test_a_deepseek_row_without_the_flag_uses_its_own_fixed_endpoint(upstream):
    reply(upstream, OPENAI_OK)

    openai.OpenAIProvider(
        row(provider_type="deepseek", model="deepseek-chat", base_url="https://8.8.8.8"), "k"
    ).complete("Hi")

    # The stored base_url is deliberately ignored, so a crafted one cannot redirect it.
    assert upstream["calls"][0]["url"] == "https://api.deepseek.com/v1/chat/completions"


def test_a_custom_gateway_uses_the_url_it_was_given(upstream):
    reply(upstream, OPENAI_OK)

    openai.OpenAIProvider(
        row(provider_type="custom", model="m", base_url="https://gateway.example.com"), "k"
    ).complete("Hi")

    assert upstream["calls"][0]["url"] == "https://gateway.example.com/v1/chat/completions"


def test_openai_reads_the_first_choice():
    result = openai.parse_response(OPENAI_OK, model="gpt-4o", provider_type="openai")

    assert result.text == "A gecko on a warm rock."
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 8


def test_a_server_reporting_only_a_total_is_costed_as_input():
    """Several OpenAI-compatible servers send no split. Input is the cheaper side, so
    this understates rather than invents."""
    result = openai.parse_response(
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 300},
        },
        model="m",
        provider_type="custom",
    )

    assert result.usage.input_tokens == 300
    assert result.usage.output_tokens == 0


def test_openai_reports_a_truncated_reply_usefully():
    with pytest.raises(ProviderError, match="maximum response length"):
        openai.parse_response(
            {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            model="m",
            provider_type="openai",
        )


def test_openai_rejects_a_response_with_no_choices():
    with pytest.raises(ProviderError, match="no choices"):
        openai.parse_response({"usage": {}}, model="m", provider_type="openai")


# ─── Ollama ──────────────────────────────────────────────────────────────────


def test_ollama_nests_its_parameters_under_options(upstream):
    """It calls the output cap num_predict and takes nothing at the top level."""
    reply(upstream, OLLAMA_OK)

    ollama.OllamaProvider(
        row(
            provider_type="ollama",
            model="llama3.2",
            max_tokens=512,
            extra_params='{"temperature": 0.2}',
        )
    ).complete("Hi")

    body = upstream["calls"][0]["body"]
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.2, "num_predict": 512}
    assert "max_tokens" not in body


def test_ollama_uses_the_configured_address(upstream):
    reply(upstream, OLLAMA_OK)

    ollama.OllamaProvider(
        row(provider_type="ollama", model="llama3.2", base_url="http://gpu-box.local:11434/")
    ).complete("Hi")

    assert upstream["calls"][0]["url"] == "http://gpu-box.local:11434/api/chat"


def test_ollama_sends_images_as_a_flat_list_of_strings(upstream):
    """Its schema has nowhere to put a media type; it sniffs the format itself."""
    reply(upstream, OLLAMA_OK)

    ollama.OllamaProvider(
        row(provider_type="ollama", model="llava", supports_images=True)
    ).complete("What is this?", images=[JPEG])

    message = upstream["calls"][0]["body"]["messages"][-1]
    assert message["images"] == [base64.b64encode(JPEG.data).decode("ascii")]
    assert message["content"] == "What is this?"


def test_a_missing_ollama_model_says_how_to_get_it(upstream):
    """By far the most common misconfiguration, and the message is the fix."""
    upstream["response"] = httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(ProviderError, match="ollama pull llama3.2"):
        ollama.OllamaProvider(row(provider_type="ollama", model="llama3.2")).complete("Hi")


def test_ollama_reports_its_own_token_counts():
    result = ollama.parse_response(OLLAMA_OK, model="llama3.2", provider_type="ollama")

    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 8


# ─── the capability flag, on every protocol ──────────────────────────────────


@pytest.mark.parametrize(
    "build",
    [
        lambda: anthropic.AnthropicProvider(row(supports_images=False), "k"),
        lambda: openai.OpenAIProvider(
            row(provider_type="openai", model="gpt-4o", supports_images=False), "k"
        ),
        lambda: ollama.OllamaProvider(
            row(provider_type="ollama", model="llama3.2", supports_images=False)
        ),
    ],
    ids=["anthropic", "openai", "ollama"],
)
def test_a_text_only_provider_refuses_an_image_before_sending_it(upstream, build):
    """The failure mode the flag exists to prevent. Without this the request goes out
    and comes back as a deserialization complaint about a JSON shape, which tells the
    user nothing about the actual problem — and costs a round trip to say it."""
    with pytest.raises(ProviderError, match="not configured to accept images"):
        build().complete("What is this?", images=[JPEG])

    assert upstream["calls"] == []


@pytest.mark.parametrize(
    "module,payload",
    [
        (anthropic, {"content": [{"type": "text", "text": ""}]}),
        (openai, {"choices": [{"message": {"content": "   "}}]}),
        (ollama, {"message": {"content": ""}}),
    ],
    ids=["anthropic", "openai", "ollama"],
)
def test_an_empty_reply_is_an_error_not_an_empty_description(module, payload):
    """Otherwise a blank description overwrites whatever was there before."""
    with pytest.raises(ProviderError, match="empty reply"):
        module.parse_response(payload, model="m", provider_type="p")


@pytest.mark.parametrize("module", [anthropic, openai, ollama], ids=["anthropic", "openai", "ollama"])
def test_a_response_that_is_not_a_dict_is_refused(module):
    with pytest.raises(ProviderError):
        module.parse_response(["unexpected"], model="m", provider_type="p")
