"""A document is found by what is written in it, and the hit says on which page.

Through the real pipeline: upload, extract, then search over HTTP. The pages are what
Video Creator's assistant (and anything else reading the library) is handed, so a hit
that names its page is what lets it go straight to the right part of the text.
"""

import pytest
from sqlmodel import select

from app.enrichment import embed, extract_text
from app.models.asset import Asset
from app.models.embedding import OWNER_PAGE, Embedding
from app.models.job import KIND_EMBED, EnrichmentJob
from app.search import vectors
from app.embeddings import PROVIDER_OLLAMA
from app.settings_store import EMBEDDING_PROVIDER, set_setting
from tests.test_search_api import StubEmbedder

TEST_USER = "user-under-test"

# Two paragraphs over the chunk size, so the file reads back as two parts and a hit on
# the second must say "Part 2".
FIRST = "Opening remarks about the harvest festival. " * 50
SECOND = "The committee approved a new aerosol dispersion protocol for the lab. " * 45
REPORT = (FIRST + "\n\n" + SECOND).encode()


def upload(client, body=REPORT, filename="committee_minutes.txt"):
    return client.post(
        "/api/assets", files=[("files", (filename, body, "text/plain"))]
    ).json()["created"][0]


def extracted(client, session, **kwargs):
    created = upload(client, **kwargs)
    asset = session.get(Asset, created["id"])
    extract_text.run(session, asset, lambda *a, **k: None)
    return asset


def search(client, q, **params):
    response = client.get("/api/search", params={"q": q, **params})
    assert response.status_code == 200
    return response.json()


# ─── keyword ─────────────────────────────────────────────────────────────────


def test_a_phrase_inside_a_document_is_found(library, session):
    asset = extracted(library, session)

    body = search(library, "dispersion protocol")

    assert [hit["asset"]["id"] for hit in body["data"]] == [asset.id]


def test_the_hit_says_which_part_of_the_document_it_is_in(library, session):
    asset = extracted(library, session)

    hit = search(library, "dispersion protocol")["data"][0]

    assert hit["page_label"] == "Part 2"
    # A text file has no pages a reader could turn to, so no number pretends otherwise.
    assert hit["page_number"] is None
    assert hit["page_id"]
    assert "«dispersion»" in hit["snippet"]
    # A page is not a moment: nothing for a video editor to cut to.
    assert hit["start_time"] is None and hit["segment_id"] is None
    pages = library.get(f"/api/assets/{asset.id}/text").json()["data"]
    assert hit["page_id"] == pages[1]["id"]


def test_a_pdf_hit_carries_its_real_page_number(library, session):
    from tests.test_extract_text import upload_document

    created = upload_document(library, ".pdf")
    asset = session.get(Asset, created["id"])
    extract_text.run(session, asset, lambda *a, **k: None)
    first_page = library.get(f"/api/assets/{asset.id}/text").json()["data"][0]
    word = max(first_page["text"].split(), key=len).strip(".,:;()")

    hit = search(library, word)["data"][0]

    assert hit["asset"]["id"] == asset.id
    assert hit["page_number"] == 1
    assert hit["page_label"] == "Page 1"


def test_documents_can_be_searched_by_type(library, session):
    asset = extracted(library, session)

    assert [h["asset"]["id"] for h in search(library, "dispersion", asset_type="document")["data"]] == [asset.id]
    assert search(library, "dispersion", asset_type="video")["data"] == []


def test_a_re_extraction_replaces_what_is_found(library, session):
    asset = extracted(library, session)

    # The file rewritten under the same row, then read again.
    from app.storage import build_storage

    storage = build_storage()
    with storage.materialise(asset.storage_key) as on_disk:
        on_disk.write_bytes(b"Only a note about sourdough now.")
    extract_text.run(session, asset, lambda *a, **k: None)

    assert search(library, "dispersion")["data"] == []
    assert [h["asset"]["id"] for h in search(library, "sourdough")["data"]] == [asset.id]


def test_deleting_the_document_takes_it_out_of_search(library, session):
    asset = extracted(library, session)

    assert library.delete(f"/api/assets/{asset.id}").status_code in (200, 204)

    assert search(library, "dispersion")["data"] == []


def test_someone_elses_document_is_not_found(library, session):
    from app.models.document import DocumentPage
    from app.search import fts

    asset = extracted(library, session)
    asset.user_id = "somebody-else"
    rows = session.exec(select(DocumentPage).where(DocumentPage.asset_id == asset.id)).all()
    for row in rows:
        row.user_id = "somebody-else"
    session.commit()
    fts.index_pages(session, asset.id, rows)

    assert search(library, "dispersion")["data"] == []


# ─── meaning ─────────────────────────────────────────────────────────────────


@pytest.fixture(name="stub_embedder")
def stub_embedder_fixture(monkeypatch):
    from app import embeddings
    from app.services import search as search_service

    stub = StubEmbedder()
    monkeypatch.setattr(embeddings, "build_embedder", lambda *_a, **_k: stub)
    monkeypatch.setattr(search_service, "build_embedder", lambda *_a, **_k: stub)
    monkeypatch.setattr(embed, "build_embedder", lambda *_a, **_k: stub)
    vectors.invalidate()
    return stub


def test_each_page_is_embedded(library, session, stub_embedder):
    asset = extracted(library, session)

    embed.run(session, asset, lambda *a, **k: None)

    pages = session.exec(
        select(Embedding).where(Embedding.asset_id == asset.id, Embedding.owner_kind == OWNER_PAGE)
    ).all()
    assert len(pages) == 2


def test_a_long_page_is_cut_before_it_is_embedded(library, session, stub_embedder, monkeypatch):
    """One input over a provider's cap fails the whole batch, so the job."""
    seen = []
    original = stub_embedder.embed
    monkeypatch.setattr(stub_embedder, "embed", lambda texts: seen.extend(texts) or original(texts))
    from tests.test_extract_text import upload_document

    created = upload_document(library, ".pdf")
    asset = session.get(Asset, created["id"])
    extract_text.run(session, asset, lambda *a, **k: None)
    from app.models.document import DocumentPage

    page = session.exec(select(DocumentPage).where(DocumentPage.asset_id == asset.id)).first()
    page.text = "word " * 5000
    session.commit()

    embed.run(session, asset, lambda *a, **k: None)

    assert max(len(text) for text in seen) <= embed.MAX_PAGE_EMBED_CHARS


def test_a_document_is_found_by_meaning_on_the_right_page(library, session, stub_embedder):
    asset = extracted(library, session)
    embed.run(session, asset, lambda *a, **k: None)

    # "weapons" and "armament" share no word with the text; the stub's geometry puts
    # them on the same axis as "aerosol dispersion".
    body = search(library, "armament")

    assert body["semantic"] is True
    hit = body["data"][0]
    assert hit["asset"]["id"] == asset.id
    assert hit["page_label"] == "Part 2"
    assert "semantic" in hit["sources"]
    assert hit["snippet"] and len(hit["snippet"]) <= 201


# ─── the chain ───────────────────────────────────────────────────────────────


def test_a_finished_extraction_queues_its_own_embedding(library, session, monkeypatch):
    """Pages nobody embeds are invisible to half of search."""
    from app.jobs import enrichment as enrichment_jobs
    from tests.test_extract_text import queued_extraction

    created = upload(library)
    set_setting(session, TEST_USER, EMBEDDING_PROVIDER, PROVIDER_OLLAMA)
    job = queued_extraction(session, created["id"])

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job.id)

    session.expire_all()
    assert session.get(EnrichmentJob, job.id).status == "done"
    embeds = session.exec(
        select(EnrichmentJob).where(
            EnrichmentJob.asset_id == created["id"], EnrichmentJob.kind == KIND_EMBED
        )
    ).all()
    assert len(embeds) == 1
