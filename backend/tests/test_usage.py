"""Cost visibility, and the discipline around it.

The rule these tests exist to hold: a figure from the pricing table is a published list
price, not a bill. Every path that produces one has to mark it, and every path that
cannot produce one has to say nothing rather than say zero.
"""

import httpx
import pytest
from sqlmodel import select

from app.enrichment import autotag, describe, summarize
from app.models.asset import Asset
from app.models.usage import KIND_AI, KIND_STT, UsageEvent
from app.providers import _upstream
from app.usage import events as usage_events
from app.usage import pricing

from tests.test_summarize import _upload_image, configure_provider

TEST_USER = "user-under-test"


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    state = {"input": 1_000_000, "output": 1_000_000, "text": "A summary.", "shape": "anthropic"}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        # A `custom` provider speaks the OpenAI protocol, so an Anthropic-shaped reply
        # would fail in the parser rather than in the code under test.
        if state["shape"] == "openai":
            payload = {
                "choices": [{"message": {"content": state["text"]}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": state["input"],
                    "completion_tokens": state["output"],
                },
            }
        else:
            payload = {
                "content": [{"type": "text", "text": state["text"]}],
                "usage": {
                    "input_tokens": state["input"],
                    "output_tokens": state["output"],
                },
            }
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


# ─── the pricing table ───────────────────────────────────────────────────────


def test_a_known_model_is_priced_from_its_family():
    # A million in and a million out at Sonnet's $3/$15.
    assert pricing.cost_for("anthropic", "claude-sonnet-4-20250514", 1_000_000, 1_000_000) == (
        18.0,
        "USD",
    )


def test_the_most_specific_match_wins():
    """"claude-3-5-haiku" has to be tested before "claude-haiku", or the cheaper model
    would be priced at the more expensive one's rate."""
    specific = pricing.cost_for("anthropic", "claude-3-5-haiku-20241022", 1_000_000, 0)
    general = pricing.cost_for("anthropic", "claude-haiku-4-5", 1_000_000, 0)

    assert specific == (0.80, "USD")
    assert general == (1.0, "USD")


def test_an_unrecognised_model_in_a_known_family_still_gets_a_figure():
    """A model released after this table was written should be the right order of
    magnitude rather than invisible."""
    assert pricing.cost_for("anthropic", "claude-something-new", 1_000_000, 0) == (3.0, "USD")


def test_ollama_is_free_and_says_so():
    """An honest zero — it runs on the user's own machine."""
    assert pricing.cost_for("ollama", "llama3.2", 5_000_000, 5_000_000) == (0.0, "USD")


@pytest.mark.parametrize("provider_type", ["custom", "", None, "something-else"])
def test_an_unpriceable_provider_returns_nothing_not_zero(provider_type):
    """Zero would read as "this was free", which is a different and wrong claim."""
    assert pricing.cost_for(provider_type, "some-model", 1_000_000, 1_000_000) is None


# ─── recording ───────────────────────────────────────────────────────────────


def test_a_run_records_what_it_spent(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session)

    summarize.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    rows = session.exec(select(UsageEvent)).all()
    assert len(rows) == 1
    assert rows[0].kind == KIND_AI
    assert rows[0].units == 2_000_000
    assert rows[0].asset_id == created["id"]
    assert rows[0].provider == "anthropic"


def test_an_estimated_cost_is_marked_as_one(library, session, upstream):
    created = _upload_image(library)
    configure_provider(session)

    summarize.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    row = session.exec(select(UsageEvent)).first()
    assert row.cost == 18.0
    assert row.cost_estimated is True


def test_an_unpriceable_run_records_the_tokens_and_no_cost(library, session, upstream):
    """A custom gateway's rate is unknowable from here. The usage is still real."""
    created = _upload_image(library)
    configure_provider(session, provider_type="custom")
    upstream["shape"] = "openai"

    summarize.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    row = session.exec(select(UsageEvent)).first()
    assert row.units == 2_000_000
    assert row.cost is None
    assert row.cost_estimated is None


def test_a_provider_reporting_no_tokens_records_nothing(library, session, upstream):
    """A zero-token row would suggest a free call rather than an unmeasured one."""
    created = _upload_image(library)
    configure_provider(session)
    upstream["input"] = 0
    upstream["output"] = 0

    summarize.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert session.exec(select(UsageEvent)).all() == []


def test_a_reply_the_provider_parser_rejects_is_not_recorded(library, session, upstream):
    """The one gap, asserted rather than left to be discovered. An empty completion
    raises inside the client, before this job holds a Completion to record — so those
    tokens go unaccounted. Closing it means letting ProviderError carry usage, which is a
    change to the provider contract rather than to accounting."""
    from app.providers.base import ProviderError

    created = _upload_image(library)
    configure_provider(session)
    upstream["text"] = "   "

    with pytest.raises(ProviderError):
        summarize.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert session.exec(select(UsageEvent)).all() == []


def test_every_llm_job_records(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)

    summarize.run(session, asset, lambda *a, **k: None)
    describe.run(session, asset, lambda *a, **k: None)
    upstream["text"] = '{"title": "A title", "tags": ["one"]}'
    autotag.run(session, asset, lambda *a, **k: None)

    assert len(session.exec(select(UsageEvent)).all()) == 3


def test_recording_never_breaks_the_work_it_measures(library, session, upstream, monkeypatch):
    """Losing a line of accounting is a far smaller problem than losing the summary the
    user just paid for."""
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)

    def explode(*args, **kwargs):
        raise RuntimeError("the pricing table is on fire")

    # Inside the accounting, not its entry point: patching `record` itself would step
    # over the very guard this asserts.
    monkeypatch.setattr(pricing, "cost_for", explode)

    summarize.run(session, asset, lambda *a, **k: None)

    session.refresh(asset)
    assert asset.summary == "A summary."


# ─── reading it back ─────────────────────────────────────────────────────────


def test_the_summary_endpoint_totals_everything(library, session, upstream):
    created = _upload_image(library)
    asset = session.get(Asset, created["id"])
    configure_provider(session)
    summarize.run(session, asset, lambda *a, **k: None)
    describe.run(session, asset, lambda *a, **k: None)

    body = library.get("/api/usage").json()["data"]

    assert body["totals"]["total_events"] == 2
    assert body["totals"]["cost"] == 36.0
    assert body["totals"]["estimated"] is True
    assert body["totals"]["tokens"] == 4_000_000
    assert body["by_provider"][0]["provider"] == "anthropic"


def test_unpriced_events_are_counted_separately(library, session, upstream):
    """When the two differ the total is a floor, not the whole story — and the UI has to
    be able to tell."""
    created = _upload_image(library)
    configure_provider(session, provider_type="custom")
    upstream["shape"] = "openai"

    summarize.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    totals = library.get("/api/usage").json()["data"]["totals"]
    assert totals["total_events"] == 1
    assert totals["priced_events"] == 0


def test_an_empty_library_reports_zero_rather_than_failing(library):
    totals = library.get("/api/usage").json()["data"]["totals"]

    assert totals["total_events"] == 0
    assert totals["cost"] == 0.0


def test_per_asset_usage(library, session, upstream):
    first = _upload_image(library)
    second = _upload_image(library)
    configure_provider(session)
    summarize.run(session, session.get(Asset, first["id"]), lambda *a, **k: None)

    mine = library.get(f"/api/usage/assets/{first['id']}").json()["data"]
    other = library.get(f"/api/usage/assets/{second['id']}").json()["data"]

    assert mine["cost"] == 18.0
    assert other["total_events"] == 0


def test_someone_elses_asset_is_a_404(library, session):
    session.add(
        Asset(id="theirs", user_id="a-different-user", name="Theirs", asset_type="image")
    )
    session.commit()

    assert library.get("/api/usage/assets/theirs").status_code == 404


def test_usage_requires_authentication(client):
    assert client.get("/api/usage").status_code == 401


def test_spend_survives_deleting_the_asset_it_was_spent_on(library, session, upstream):
    """`asset_id` is deliberately not a foreign key. A spend history that shrinks when
    you tidy your library is not a spend history."""
    created = _upload_image(library)
    configure_provider(session)
    summarize.run(session, session.get(Asset, created["id"]), lambda *a, **k: None)

    assert library.delete(f"/api/assets/{created['id']}").status_code == 204

    session.expire_all()
    assert library.get("/api/usage").json()["data"]["totals"]["cost"] == 18.0


# ─── transcription ───────────────────────────────────────────────────────────


def test_transcription_records_seconds_with_no_invented_price(library, session):
    """Deepgram bills per minute and its rate is not in the table. The event is still
    recorded, so a library total does not silently exclude transcription."""
    usage_events.record(
        session,
        user_id=TEST_USER,
        kind=KIND_STT,
        units=3600,
        unit_type="seconds",
        provider="deepgram",
        model="nova-3",
    )

    totals = library.get("/api/usage").json()["data"]["totals"]
    assert totals["seconds"] == 3600
    assert totals["total_events"] == 1
    assert totals["priced_events"] == 0
    assert totals["cost"] == 0.0
