"""The assistant finding a page of a PDF and proposing a picture of it.

Nothing is made until the person applies the proposal, like every other proposal. The PDF is
written by hand here, because the shared fixture has one page and finding the right page
among several is the whole point.
"""

import json

from sqlmodel import select

from app.assistant import tools
from app.models.asset import Asset
from app.services import pdf_grab
from app.enrichment import extract_text
from tests.test_assistant import new_session, reply, run
from tests.test_assistant import fake_suite_fixture as fake_suite  # noqa: F401 - the fixture


def make_pdf(pages: list[str]) -> bytes:
    """A real multi-page PDF with one line of Helvetica per page."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font = 3 + 2 * len(pages)
    for i, line in enumerate(pages):
        stream = f"BT /F1 24 Tf 72 700 Td ({line}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2 * i} 0 R >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


PAGES = ["Cover page of the report", "Contents and preface", "Table of signal types and bands",
         "Appendix on method"]


def a_pdf(library, session, pages=PAGES, extract=True):
    created = library.post("/api/assets", files=[
        ("files", ("report.pdf", make_pdf(pages), "application/pdf"))]).json()["created"][0]
    asset = session.get(Asset, created["id"])
    if extract:
        extract_text.run(session, asset, lambda *a, **k: None)
    return asset


def saw(fake_suite, call):
    return json.dumps(fake_suite.chats[call]["messages"])


def test_the_page_about_a_subject_is_found_read_and_proposed_but_not_made(
        library, session, fake_suite):
    pdf = a_pdf(library, session)
    fake_suite.script += [
        reply("", ("find_pages", {"asset_id": pdf.id, "query": "signal"})),
        reply("", ("read_page", {"asset_id": pdf.id, "page": 3})),
        reply("", ("propose_grab", {"asset_id": pdf.id, "page": 3, "name": "Signal table",
                                    "reason": "The table of signal types."})),
        reply("Page 3 has it."),
    ]
    done = run(library, new_session(library), "Grab the table about signals")

    assert "page 3" in saw(fake_suite, 1)
    assert "Table of signal types and bands" in saw(fake_suite, 2)
    (entry,) = done["proposal"]["grabs"]
    assert (entry["asset_id"], entry["page"], entry["name"]) == (pdf.id, 3, "Signal table")
    # Proposed, not made.
    assert session.exec(select(Asset).where(Asset.source == "pdf_page")).all() == []

    res = library.post(f"/api/assistant/turns/{done['id']}/apply")
    assert res.status_code == 200, res.text
    (result,) = res.json()["data"]
    assert result["grab_id"] == entry["grab_id"] and result["skipped"] == []
    picture = library.get(f"/api/assets/{result['created_asset_id']}").json()["data"]
    assert (picture["asset_type"], picture["source_page"], picture["parent_asset_id"],
            picture["name"]) == ("image", 3, pdf.id, "Signal table")


def test_apply_can_take_one_grab_and_leave_another(library, session, fake_suite):
    pdf = a_pdf(library, session)
    fake_suite.script += [
        reply("", ("propose_grab", {"asset_id": pdf.id, "page": 1}),
              ("propose_grab", {"asset_id": pdf.id, "page": 2})),
        reply("Two pages."),
    ]
    done = run(library, new_session(library))
    first, second = done["proposal"]["grabs"]

    res = library.post(f"/api/assistant/turns/{done['id']}/apply",
                       json={"grab_ids": [second["grab_id"]]})

    (result,) = res.json()["data"]
    assert result["grab_id"] == second["grab_id"]
    pictures = session.exec(select(Asset).where(Asset.source == "pdf_page")).all()
    assert [p.source_page for p in pictures] == [2]


def test_a_page_that_does_not_exist_is_refused_to_the_model_and_the_same_page_twice_is_one(
        library, session, fake_suite):
    pdf = a_pdf(library, session)
    fake_suite.script += [
        reply("", ("propose_grab", {"asset_id": pdf.id, "page": 9}),
              ("propose_grab", {"asset_id": pdf.id, "page": 1}),
              ("propose_grab", {"asset_id": pdf.id, "page": 1})),
        reply("Page 1 only."),
    ]
    done = run(library, new_session(library))

    assert [g["page"] for g in done["proposal"]["grabs"]] == [1]
    seen = saw(fake_suite, 1)
    assert "no page 9" in seen and "Already proposed" in seen


def test_a_scanned_pdf_says_it_cannot_be_searched(library, session, fake_suite):
    pdf = a_pdf(library, session, extract=False)
    fake_suite.script += [
        reply("", ("find_pages", {"asset_id": pdf.id, "query": "signal"})),
        reply("Which page?"),
    ]
    run(library, new_session(library))

    assert "no readable text" in saw(fake_suite, 1)


def test_only_a_pdf_can_be_proposed_and_someone_elses_is_not_found(library, session, fake_suite):
    pdf = a_pdf(library, session)
    docx = library.post("/api/assets", files=[(
        "files", ("note.txt", b"hello", "text/plain"))]).json()["created"][0]
    pdf.user_id = "somebody-else"
    session.add(pdf)
    session.commit()
    fake_suite.script += [
        reply("", ("propose_grab", {"asset_id": docx["id"], "page": 1}),
              ("propose_grab", {"asset_id": pdf.id, "page": 1})),
        reply("Neither."),
    ]
    done = run(library, new_session(library))

    assert done["proposal"] is None
    seen = saw(fake_suite, 1)
    assert "Only a PDF" in seen and "No such asset" in seen


def test_a_pdf_hit_in_search_names_its_page_and_get_asset_its_page_count(
        library, session, fake_suite):
    pdf = a_pdf(library, session)
    fake_suite.script += [
        reply("", ("search_assets", {"query": "signal"}), ("get_asset", {"asset_id": pdf.id})),
        reply("Found."),
    ]
    run(library, new_session(library))

    seen = saw(fake_suite, 1)
    assert "on page 3" in seen and "pages: 4" in seen
