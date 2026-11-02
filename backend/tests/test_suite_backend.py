"""Enrichment and embeddings on Gecko Suite (`AI_BACKEND=suite`), with the suite faked.

Enrichment is background work with no user session, so the suite is called as GAM on the
owner's behalf. These check who is calling and for whom, which job each kind of
enrichment names, what the spend is attributed to, that nothing is counted twice, and that
a suite that is down or unrouted degrades the way an unconfigured provider always has.
"""

import json
from typing import Dict, List, Optional

import httpx
import pytest

from app import suite
from app.config import Settings, settings
from app.embeddings import build_embedder
from app.enrichment import describe, summarize
from app.models.asset import Asset
from app.models.usage import UsageEvent
from app.providers import build_provider
from app.providers.base import ProviderError, ProviderUnavailable

from tests.test_summarize import SUMMARY, TEST_USER, _upload_image, add_transcript

SECRET = "gam-app-secret"


def model(label="Claude Sonnet 5.5", model_id="claude-sonnet-5-5", capabilities=None) -> dict:
    return {"id": "m1", "label": label, "model_id": model_id, "vendor": "anthropic",
            "capabilities": capabilities or ["chat", "vision", "tools"], "embed_dims": None}


EMBEDDER = {"id": "e1", "label": "text-embedding-3-small", "model_id": "text-embedding-3-small",
            "vendor": "openai", "capabilities": ["embed"], "embed_dims": 1536}


class FakeSuite:
    def __init__(self):
        self.chats: List[dict] = []
        self.embeds: List[dict] = []
        self.resolved: List[tuple] = []
        self.headers: List[Dict[str, str]] = []
        self.routing: Dict[str, Optional[dict]] = {}
        self.default: Optional[dict] = model()
        self.text = SUMMARY
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        self.headers.append(dict(request.headers))
        path = request.url.path
        if path == "/api/v1/resolve":
            task = request.url.params["task"]
            self.resolved.append((task, request.url.params.get("app")))
            if task == "embed.text":
                return httpx.Response(200, json={"data": {"model": self.routing.get(task, EMBEDDER)}})
            return httpx.Response(200, json={"data": {"model": self.routing.get(task, self.default)}})
        body = json.loads(request.content)
        if path == "/api/v1/ai/chat":
            self.chats.append(body)
            return httpx.Response(200, json={"data": {
                "text": self.text, "tool_calls": [], "stop": "end", "refusal_category": None,
                "message": {"role": "assistant", "text": self.text, "tool_calls": [], "raw": {
                    "protocol": "anthropic", "message": {"role": "assistant", "content": []}}},
                "usage": {"input_tokens": 700, "output_tokens": 30, "cache_read_tokens": 0,
                          "cache_write_tokens": 0},
                "model": "claude-sonnet-5-5", "model_ref": "m1", "provider": "Claude",
                "vendor": "anthropic", "task": body.get("task"), "cost": 0.004, "currency": "USD",
                "cost_estimated": True, "usage_event_id": "u"}})
        if path == "/api/v1/ai/embed":
            self.embeds.append(body)
            return httpx.Response(200, json={"data": {
                "vectors": [[0.5] * 512 for _ in body["texts"]], "model": "text-embedding-3-small",
                "model_ref": "e1", "dimensions": 512, "input_tokens": 9, "cost": None,
                "currency": None, "usage_event_id": "u"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


@pytest.fixture(name="fake_suite")
def fake_suite_fixture(monkeypatch):
    fake = FakeSuite()
    monkeypatch.setattr(settings, "ai_backend", "suite")
    monkeypatch.setattr(settings, "suite_app_secret", SECRET)
    monkeypatch.setattr(suite, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


def nothing(*args, **kwargs):
    return None


# ─── generation ──────────────────────────────────────────────────────────────


def test_describing_runs_as_gam_on_the_owners_behalf_and_is_attributed_to_the_asset(
        library, session, fake_suite):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])

    describe.run(session, asset, nothing)

    sent = fake_suite.chats[0]
    assert (sent["task"], sent["app"]) == ("enrich.describe", "gam")
    assert sent["subject_ref"] == f"gam:asset:{asset.id}"
    # An image goes as an image part, for a model that can see.
    assert any(p["type"] == "image" for p in sent["messages"][0]["content"])
    # Called as the app, for the person who owns the asset: no session to forward.
    caller = fake_suite.headers[-1]
    assert caller["x-gecko-app"] == "gam" and caller["x-gecko-app-secret"] == SECRET
    assert caller["x-on-behalf-of"] == TEST_USER and "authorization" not in caller

    session.refresh(asset)
    assert asset.description == SUMMARY
    # The suite counted the spend. Writing it here too would count it twice.
    assert session.query(UsageEvent).count() == 0


def test_each_kind_of_enrichment_names_its_own_job(library, session, fake_suite):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])

    summarize.run(session, asset, nothing)
    describe.run(session, asset, nothing)

    # So an administrator can give summarising a cheap model and describing a seeing one.
    assert [c["task"] for c in fake_suite.chats] == ["enrich.summarize", "enrich.describe"]


def test_a_model_that_cannot_see_is_not_sent_the_picture(library, session, fake_suite):
    fake_suite.routing["enrich.describe"] = model("DeepSeek Chat", "deepseek-chat", ["chat"])
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    add_transcript(session, asset.id, ["Giordano on neuroweapons."])

    describe.run(session, asset, nothing)

    sent = fake_suite.chats[0]["messages"]
    # No message carries parts, so no image went to a model that cannot see.
    assert not any(isinstance(m["content"], list) for m in sent)


def test_nothing_routed_reads_as_no_provider_like_always(session, fake_suite):
    fake_suite.default = None
    assert build_provider(session, TEST_USER) is None


def test_a_suite_that_is_down_says_so_instead_of_pretending_nothing_is_set_up(session, fake_suite):
    fake_suite.down = True
    with pytest.raises(ProviderError) as excinfo:
        build_provider(session, TEST_USER)
    assert "Gecko Suite can't be reached" in str(excinfo.value)
    assert not isinstance(excinfo.value, ProviderUnavailable)


def test_queueing_enrichment_while_the_suite_is_down_is_a_503_not_a_crash(
        library, session, fake_suite):
    created = _upload_image(library)
    fake_suite.down = True
    res = library.post(f"/api/assets/{created['id']}/summarize")
    assert res.status_code == 503 and res.json()["detail"]["code"] == "ai_unavailable"
    # The library itself is unaffected.
    assert library.get("/api/assets").status_code == 200


def test_a_refusal_comes_back_as_a_provider_error(session, fake_suite, monkeypatch):
    llm = build_provider(session, TEST_USER)
    monkeypatch.setattr(fake_suite, "text", "")
    fake_suite.down = False

    def refuse(request):
        return httpx.Response(409, json={"detail": {
            "code": "provider_key_missing", "message": "Claude has no usable API key. Set it in Gecko Suite."}})

    monkeypatch.setattr(suite, "TRANSPORT", httpx.MockTransport(refuse))
    with pytest.raises(ProviderError) as excinfo:
        llm.complete("Hello")
    assert "Set it in Gecko Suite" in str(excinfo.value)


# ─── embeddings ──────────────────────────────────────────────────────────────


def test_the_embedder_knows_its_model_and_width_without_embedding_anything(session, fake_suite):
    embedder = build_embedder(session, TEST_USER)
    assert embedder is not None
    # What search compares stored vectors by, and what coverage reports.
    assert embedder.model == "text-embedding-3-small"
    assert embedder.dimensions == 512  # SUITE_EMBEDDING_DIMENSIONS, not the model's 1536
    assert fake_suite.embeds == []


def test_embedding_asks_for_the_width_the_index_already_has(session, fake_suite):
    embedder = build_embedder(session, TEST_USER)
    vectors = embedder.embed(["a", "b"])
    assert len(vectors) == 2 and len(vectors[0]) == 512
    sent = fake_suite.embeds[0]
    assert sent["dimensions"] == 512 and sent["app"] == "gam" and sent["texts"] == ["a", "b"]


def test_no_embedding_model_routed_is_no_embedder(session, fake_suite):
    fake_suite.routing["embed.text"] = None
    assert build_embedder(session, TEST_USER) is None


def test_semantic_search_degrades_to_keywords_when_the_suite_is_down(session, fake_suite):
    fake_suite.down = True
    # None, exactly as for an unconfigured embedder: search answers with keywords.
    assert build_embedder(session, TEST_USER) is None


# ─── configuration ───────────────────────────────────────────────────────────


def test_the_suite_backend_cannot_start_without_its_credential():
    with pytest.raises(ValueError) as excinfo:
        Settings(jwt_secret_key="a-strong-enough-test-secret", ai_backend="suite")
    assert "SUITE_APP_SECRET" in str(excinfo.value)
    ok = Settings(jwt_secret_key="a-strong-enough-test-secret", ai_backend="suite",
                  suite_app_secret="s")
    assert ok.ai_backend == "suite"


def test_local_is_the_default_and_a_typo_is_refused():
    assert Settings(jwt_secret_key="a-strong-enough-test-secret").ai_backend == "local"
    with pytest.raises(ValueError):
        Settings(jwt_secret_key="a-strong-enough-test-secret", ai_backend="cloud")


def test_local_providers_still_record_their_own_usage(library, session):
    """The other half of "nothing is counted twice": with the local backend nothing has
    changed, so the suite's skip must not have swallowed it."""
    from app.usage import events as usage_events
    from app.providers.base import Completion, Usage

    usage_events.record_completion(
        session, None, TEST_USER,
        Completion(text="x", usage=Usage(100, 10), model="claude-sonnet-5-5", provider_type="anthropic"),
    )
    assert session.query(UsageEvent).count() == 1
