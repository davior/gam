"""Search over HTTP, with a stub embedder of known geometry.

The stub is not a claim about retrieval quality — that is the embedding model's job and
needs a real provider to judge. It has *deliberate* geometry so the plumbing either
works or visibly does not: does a semantic-only match surface, does fusion promote
agreement, does an exact phrase still win.
"""

import pytest

from app.models.asset import Asset
from app.models.transcript import TranscriptSegment
from app.search import fts, vectors


class StubEmbedder:
    """Maps text to a vector by the concepts it mentions.

    Each concept is an axis, so "nano weapons" and a paraphrase that mentions the same
    concept land near each other without sharing a word — which is the property real
    embeddings have and keyword search does not.
    """

    CONCEPTS = ("weapons", "economics", "cooking", "weather")

    model = "stub-embed-v1"
    dimensions = len(CONCEPTS)

    TERMS = {
        "weapons": {"nano", "weapon", "weapons", "aerosol", "dispersion", "armament", "deploying"},
        "economics": {"industrial", "revolution", "economy", "davos", "capital"},
        "cooking": {"recipe", "sandwich", "bake", "kitchen"},
        "weather": {"rain", "storm", "sunshine", "forecast"},
    }

    def embed(self, texts):
        out = []
        for text in texts:
            words = {w.strip(".,").lower() for w in (text or "").split()}
            vector = [
                float(len(words & self.TERMS[concept])) for concept in self.CONCEPTS
            ]
            if not any(vector):
                vector = [0.001] * len(self.CONCEPTS)
            out.append(vector)
        return out


@pytest.fixture(name="embedded_library")
def embedded_library_fixture(library, session, monkeypatch):
    """A library with two assets, both indexed for keyword and semantic search."""
    from app import embeddings

    stub = StubEmbedder()
    monkeypatch.setattr(embeddings, "build_embedder", lambda *_a, **_k: stub)
    # The search service imports the symbol directly.
    from app.services import search as search_service

    monkeypatch.setattr(search_service, "build_embedder", lambda *_a, **_k: stub)
    vectors.invalidate()

    def add(name, description, lines):
        asset = Asset(
            user_id="user-under-test",
            name=name,
            description=description,
            asset_type="video",
            source="local_upload",
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        fts.index_asset(session, asset)

        segments = []
        for index, (text, start) in enumerate(lines):
            segment = TranscriptSegment(
                asset_id=asset.id,
                user_id=asset.user_id,
                idx=index,
                text=text,
                start_time=start,
                end_time=start + 5,
            )
            session.add(segment)
            segments.append(segment)
        session.commit()
        fts.index_segments(session, asset.id, segments)

        computed = stub.embed([s.text for s in segments])
        vectors.replace_for_asset(
            session,
            user_id=asset.user_id,
            asset_id=asset.id,
            owner_kind="segment",
            rows=list(zip((s.id for s in segments), computed)),
            model=stub.model,
        )
        return asset

    giordano = add(
        "Giordano interview",
        "Testimony before a committee",
        [
            ("Good afternoon and welcome to the session.", 0.0),
            ("We are looking at deploying nano weapons via aerosol dispersion.", 412.0),
            ("The forecast calls for rain later in the week.", 900.0),
        ],
    )
    schwab = add(
        "Davos panel",
        "Opening remarks",
        [
            ("The fourth industrial revolution changes the economy entirely.", 120.0),
            ("I made a sandwich in the kitchen this morning.", 300.0),
        ],
    )
    return {"giordano": giordano, "schwab": schwab, "client": library, "stub": stub}


def search(client, q, **params):
    return client.get("/api/search", params={"q": q, **params}).json()


# ─── the timestamp is the product ────────────────────────────────────────────


def test_a_spoken_hit_returns_the_second_it_was_said(embedded_library):
    body = search(embedded_library["client"], "nano weapons")

    assert body["total"] >= 1
    top = body["data"][0]
    assert top["asset"]["name"] == "Giordano interview"
    assert top["start_time"] == 412.0
    assert "«nano»" in top["snippet"] or "«weapons»" in top["snippet"]


def test_the_snippet_is_the_line_not_the_whole_transcript(embedded_library):
    top = search(embedded_library["client"], "aerosol")["data"][0]
    assert len(top["snippet"]) < 200
    assert "aerosol" in top["snippet"].lower()


# ─── exact phrases must still win ────────────────────────────────────────────


def test_an_exact_phrase_still_ranks_first(embedded_library):
    """Guards the fusion from becoming the vector index in disguise. A verbatim quote is
    exactly what keyword search is for, and it must not be diluted."""
    body = search(embedded_library["client"], "fourth industrial revolution")

    assert body["data"][0]["asset"]["name"] == "Davos panel"
    assert "keyword" in body["data"][0]["sources"]


def test_a_name_only_match_is_found(embedded_library):
    """Nobody says the filename out loud, so this can only come from the metadata
    index."""
    body = search(embedded_library["client"], "Davos")
    assert body["data"][0]["asset"]["name"] == "Davos panel"


# ─── the semantic half ───────────────────────────────────────────────────────


def test_a_paraphrase_with_no_shared_words_still_finds_it(embedded_library, session):
    """The reason semantic search exists at all.

    "armament" appears nowhere in the transcript. The first assertion proves keyword
    search genuinely cannot answer this — without it, the test would pass even if
    fusion were ignoring the semantic list entirely.
    """
    assert fts.search_segments(session, "user-under-test", "armament") == []

    body = search(embedded_library["client"], "armament")

    assert body["semantic"] is True
    assert body["total"] >= 1
    assert body["data"][0]["asset"]["name"] == "Giordano interview"
    assert body["data"][0]["sources"] == ["semantic"]


def test_the_response_says_whether_the_semantic_half_ran(library):
    """"No results" means something different when only half the search happened."""
    body = search(library, "anything")
    assert body["semantic"] is False


def test_an_unconfigured_provider_degrades_rather_than_fails(library, session):
    """A new library has no embedding provider, and search must still work."""
    asset = Asset(
        user_id="user-under-test", name="Giordano interview", asset_type="video", source="local_upload"
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    fts.index_asset(session, asset)

    body = search(library, "giordano")
    assert body["semantic"] is False
    assert body["total"] == 1


def test_a_failing_provider_degrades_and_says_so(embedded_library, monkeypatch):
    from app.embeddings import EmbeddingError
    from app.services import search as search_service

    def broken(*_a, **_k):
        raise EmbeddingError("OpenAI rejected the API key")

    monkeypatch.setattr(search_service, "build_embedder", broken)

    body = search(embedded_library["client"], "nano weapons")

    # Keyword results still arrive, and the failure is reported rather than swallowed.
    assert body["total"] >= 1
    assert body["semantic"] is False
    assert "API key" in (body["semantic_error"] or "")


# ─── shape and scoping ───────────────────────────────────────────────────────


def test_one_row_per_asset_with_the_rest_counted(embedded_library):
    """Both Giordano lines mention deployment concepts, so it matches twice."""
    body = search(embedded_library["client"], "nano weapons aerosol dispersion")

    giordano = [h for h in body["data"] if h["asset"]["name"] == "Giordano interview"]
    assert len(giordano) == 1
    assert giordano[0]["other_matches"] >= 1


def test_results_can_be_filtered_by_type(embedded_library):
    body = search(embedded_library["client"], "nano weapons", asset_type="image")
    assert body["total"] == 0


def test_an_unknown_type_is_rejected(embedded_library):
    response = embedded_library["client"].get(
        "/api/search", params={"q": "x", "asset_type": "hologram"}
    )
    assert response.status_code == 400


def test_an_empty_query_returns_nothing(embedded_library):
    assert search(embedded_library["client"], "")["total"] == 0
    assert search(embedded_library["client"], "   ")["total"] == 0


def test_search_only_covers_your_own_library(library, session):
    theirs = Asset(
        user_id="somebody-else", name="Giordano interview", asset_type="video", source="local_upload"
    )
    session.add(theirs)
    session.commit()
    fts.index_asset(session, theirs)

    assert search(library, "giordano")["total"] == 0


def test_search_requires_authentication(client):
    assert client.get("/api/search", params={"q": "x"}).status_code == 401
