"""The extract_text job — the step that stops documents refusing enrichment.

Two behaviours carry most of the cases: what each format's unit is (a PDF page, a slide,
a worksheet, a chunk), and the precedence question this job created — a PDF has a
first-page thumbnail, so something has to decide between reading its words and looking
at a picture of its cover.
"""

from pathlib import Path

import httpx
import pytest
from sqlmodel import col, select

from app.enrichment import extract_text, source, summarize
from app.jobs import enrichment as enrichment_jobs
from app.jobs.runner import JobCancelled
from app.models.asset import Asset
from app.models.document import DocumentPage
from app.models.job import EnrichmentJob, KIND_EXTRACT_TEXT
from app.providers import _upstream
from tests.test_summarize import configure_provider, sent_prompt

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"

MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
}


def upload_document(client, extension=".pdf", name=None):
    filename = name or f"sample_document{extension}"
    return client.post(
        "/api/assets",
        files=[
            (
                "files",
                (
                    filename,
                    (FIXTURES / f"sample_document{extension}").read_bytes(),
                    MIME[extension],
                ),
            )
        ],
    ).json()["created"][0]


@pytest.fixture(name="upstream")
def upstream_fixture(monkeypatch):
    """Answer every completion with a canned summary, and record what was asked.

    A local copy rather than a shared one, matching test_describe.py — the assertion
    helpers are imported, the fixture is not.
    """
    state = {"calls": []}

    def fake_post_json(url, *, headers=None, json_body, timeout, label, **kwargs):
        state["calls"].append({"url": url, "body": json_body})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "A quarterly report."}],
                "usage": {"input_tokens": 120, "output_tokens": 20},
            },
        )

    monkeypatch.setattr(_upstream, "post_json", fake_post_json)
    return state


def pages_of(session, asset_id):
    return session.exec(
        select(DocumentPage)
        .where(DocumentPage.asset_id == asset_id)
        .order_by(col(DocumentPage.idx))
    ).all()


@pytest.mark.parametrize(
    "extension,expected_chunks",
    [(".pdf", 1), (".docx", 1), (".pptx", 2), (".xlsx", 2), (".txt", 1)],
)
def test_every_supported_format_yields_its_text(library, session, extension, expected_chunks):
    created = upload_document(library, extension)
    asset = session.get(Asset, created["id"])

    extract_text.run(session, asset, lambda *a, **k: None)

    pages = pages_of(session, asset.id)
    assert len(pages) == expected_chunks
    assert "Gecko Asset Manager" in pages[0].text


def test_a_pdf_page_carries_its_real_page_number(library, session):
    created = upload_document(library, ".pdf")
    asset = session.get(Asset, created["id"])

    extract_text.run(session, asset, lambda *a, **k: None)

    page = pages_of(session, asset.id)[0]
    assert page.page_number == 1
    assert page.label == "Page 1"


def test_a_docx_has_no_page_number_because_it_has_no_pages(library, session):
    """Numbering the chunks would put a number in a column whose contract is that it
    means the page the reader can turn to."""
    created = upload_document(library, ".docx")
    asset = session.get(Asset, created["id"])

    extract_text.run(session, asset, lambda *a, **k: None)

    page = pages_of(session, asset.id)[0]
    assert page.page_number is None
    assert page.label == "Part 1"


def test_a_docx_table_is_read_as_well_as_its_paragraphs(library, session):
    """A file that carries its content in a table is the case a paragraphs-only reader
    gets wrong while looking like it worked."""
    created = upload_document(library, ".docx")
    asset = session.get(Asset, created["id"])

    extract_text.run(session, asset, lambda *a, **k: None)

    assert "python-docx" in pages_of(session, asset.id)[0].text


def test_speaker_notes_are_read(library, session):
    """A deck's argument usually lives in the notes; the slide says only 'Q3 Revenue'."""
    created = upload_document(library, ".pptx")
    asset = session.get(Asset, created["id"])

    extract_text.run(session, asset, lambda *a, **k: None)

    assert "search demo" in pages_of(session, asset.id)[0].text


def test_each_worksheet_is_its_own_row_named_after_itself(library, session):
    created = upload_document(library, ".xlsx")
    asset = session.get(Asset, created["id"])

    extract_text.run(session, asset, lambda *a, **k: None)

    pages = pages_of(session, asset.id)
    assert [p.label for p in pages] == ["Sheet 'Overview'", "Sheet 'Costs'"]
    assert "Enrichment spend" in pages[1].text


def test_an_unsupported_format_is_refused_by_name(library, session):
    """"We cannot read .rtf" is actionable; "extraction failed" invites a re-run that
    cannot work."""
    created = upload_document(library, ".txt", name="notes.rtf")
    asset = session.get(Asset, created["id"])

    with pytest.raises(extract_text.TextExtractionError, match=r"\.rtf"):
        extract_text.run(session, asset, lambda *a, **k: None)


def test_a_re_run_replaces_rather_than_appends(library, session):
    created = upload_document(library, ".pptx")
    asset = session.get(Asset, created["id"])

    extract_text.run(session, asset, lambda *a, **k: None)
    extract_text.run(session, asset, lambda *a, **k: None)

    assert len(pages_of(session, asset.id)) == 2


def test_cancelling_mid_extraction_writes_nothing(library, session):
    """The progress callback is the cancellation checkpoint, so a cancel lands between
    pages — and must not leave half a document behind."""
    created = upload_document(library, ".pptx")
    asset = session.get(Asset, created["id"])

    def cancel_immediately(*args, **kwargs):
        raise JobCancelled("job-under-test")

    with pytest.raises(JobCancelled):
        extract_text.run(session, asset, cancel_immediately)

    assert pages_of(session, asset.id) == []


def test_extracted_text_beats_the_first_page_thumbnail(library, session, upstream):
    """The precedence trap this job created.

    Every PDF gets a first-page thumbnail at ingest, so before the document branch was
    placed above the poster branch a PDF sent to a vision provider was summarised from a
    picture of its cover instead of its words.
    """
    created = upload_document(library, ".pdf")
    asset = session.get(Asset, created["id"])
    extract_text.run(session, asset, lambda *a, **k: None)
    asset.thumb_key = "thumbs/pretend-this-exists.jpg"
    session.commit()
    configure_provider(session, supports_images=True)

    material = source.gather(session, asset, supports_images=True)

    assert material.kind == source.FROM_DOCUMENT
    assert "Gecko Asset Manager" in material.text

    summarize.run(session, asset, lambda *a, **k: None)
    assert "Text of the document" in sent_prompt(upstream)


def test_deleting_the_asset_takes_its_pages_with_it(library, session):
    created = upload_document(library, ".pdf")
    asset = session.get(Asset, created["id"])
    extract_text.run(session, asset, lambda *a, **k: None)

    assert library.delete(f"/api/assets/{asset.id}").status_code == 204

    assert pages_of(session, created["id"]) == []


# --- the API ----------------------------------------------------------------------


def test_the_endpoint_queues_a_job_without_a_provider(library, session):
    """The one enrichment action that must not require an LLM: it is the step that runs
    before one is any use."""
    created = upload_document(library, ".pdf")

    response = library.post(f"/api/assets/{created['id']}/extract-text")

    assert response.status_code == 202
    assert response.json()["data"]["action"] == KIND_EXTRACT_TEXT


def test_a_file_nothing_can_read_is_refused_before_it_is_queued(library):
    created = upload_document(library, ".txt", name="notes.rtf")

    response = library.post(f"/api/assets/{created['id']}/extract-text")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "not_extractable"


def test_a_video_cannot_have_its_text_extracted(library, session):
    created = upload_document(library, ".pdf")
    asset = session.get(Asset, created["id"])
    asset.asset_type = "video"
    session.commit()

    response = library.post(f"/api/assets/{asset.id}/extract-text")

    assert response.status_code == 400


def test_two_runs_at_once_are_refused(library, session):
    created = upload_document(library, ".pdf")

    first = library.post(f"/api/assets/{created['id']}/extract-text")
    second = library.post(f"/api/assets/{created['id']}/extract-text")

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "already_running"


def test_somebody_elses_asset_is_not_there(library):
    response = library.post("/api/assets/does-not-exist/extract-text")
    assert response.status_code == 404


def test_the_text_endpoint_returns_the_pages_in_order(library, session):
    created = upload_document(library, ".xlsx")
    asset = session.get(Asset, created["id"])
    extract_text.run(session, asset, lambda *a, **k: None)

    body = library.get(f"/api/assets/{asset.id}/text").json()

    assert body["total"] == 2
    assert [row["label"] for row in body["data"]] == ["Sheet 'Overview'", "Sheet 'Costs'"]


def test_the_text_endpoint_is_empty_before_extraction_runs(library, session):
    created = upload_document(library, ".pdf")

    body = library.get(f"/api/assets/{created['id']}/text").json()

    assert body["data"] == []


def test_the_worker_runs_it_end_to_end(library, session, monkeypatch):
    created = upload_document(library, ".pptx")
    job = library.post(f"/api/assets/{created['id']}/extract-text").json()["data"]

    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])
    session.expire_all()

    row = session.get(EnrichmentJob, job["id"])
    assert row.status == "done"
    assert row.detail == "2 slides"
    assert len(pages_of(session, created["id"])) == 2


def test_a_worker_failure_is_reported_in_the_users_words(library, session, monkeypatch):
    """Not "crashed": TextExtractionError has to be in the dispatcher's exception ladder
    or the ladder's bare Exception branch swallows the sentence."""
    created = upload_document(library, ".pdf")
    job = library.post(f"/api/assets/{created['id']}/extract-text").json()["data"]

    def explode(*args, **kwargs):
        raise extract_text.TextExtractionError("The file could not be read.")

    monkeypatch.setattr(enrichment_jobs, "run_extract_text", explode)
    queue = enrichment_jobs.queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    enrichment_jobs._run_job(job["id"])
    session.expire_all()

    row = session.get(EnrichmentJob, job["id"])
    assert row.status == "error"
    assert row.error_message == "The file could not be read."
