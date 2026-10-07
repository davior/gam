"""The generate job, end to end, with fal.ai stubbed at the httpx seam.

Everything on GAM's side runs for real: the request body is built from the catalogue
row, the bases are normalised by Pillow, the result is streamed through the capped
download, ingested by `ingest_file` (probe, thumbnail, index), costed, and read back
through the API. What fal says is the one assumption — see `fal_stub.py`.
"""

import base64
import io
import json

import httpx
import pytest
from PIL import Image
from sqlmodel import select

from app.config import settings
from app.embeddings import PROVIDER_OLLAMA
from app.jobs import enrichment as enrichment_jobs
from app.media_tools import ffmpeg_available
from app.models.asset import Asset
from app.models.job import KIND_EMBED, KIND_TRANSCRIBE, EnrichmentJob
from app.models.usage import UsageEvent
from app.settings_store import DEEPGRAM_API_KEY, EMBEDDING_PROVIDER, set_setting
from tests.fal_stub import (  # noqa: F401 - `fal` is a fixture
    CDN,
    FAL_KEY,
    FIXTURES,
    I2I,
    I2V,
    T2I,
    TEST_USER,
    ProcessDied,
    fal,
    run_job,
    seed_models,
    set_fal_key,
    upload,
)

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")

PROMPT = "A red fox curled up in fresh snow at golden hour, shallow depth of field"


@pytest.fixture
def models(session):
    return seed_models(session)


@pytest.fixture
def ready(session, models, fal):
    """A catalogue, a key, and fal standing by."""
    set_fal_key(session)
    return models


def _start(library, body):
    response = library.post("/api/generate", json=body)
    assert response.status_code == 202, response.text
    return response.json()["data"]["id"]


def _generate(library, session, monkeypatch, body):
    job_id = _start(library, body)
    run_job(session, monkeypatch, job_id)
    return job_id


def _activity(library, job_id):
    return library.get(f"/api/activity/enrichment/{job_id}").json()["data"]


def _generated(session):
    return session.exec(select(Asset).where(Asset.source == "ai_generated")).all()


def _usage(session):
    return session.exec(select(UsageEvent).where(UsageEvent.provider == "fal.ai")).all()


def _decoded(uri):
    header, _, payload = uri.partition(",")
    return header, Image.open(io.BytesIO(base64.b64decode(payload)))


# ─── text → image ────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_text_to_image_end_to_end(library, session, monkeypatch, ready, fal):
    job_id = _generate(
        library,
        session,
        monkeypatch,
        {
            "model_id": ready[T2I].id,
            "prompt": PROMPT,
            "params": {"image_size": "square_hd", "seed": 42, "num_outputs": 1},
        },
    )

    job = _activity(library, job_id)
    assert job["status"] == "done", job["error_message"]
    assert job["action"] == "generate"
    assert job["model"] == T2I
    assert job["detail"] == "1 image"

    (asset,) = _generated(session)
    assert job["result_asset_id"] == asset.id
    assert job["result_asset_ids"] == [asset.id]

    # What fal was sent: catalogue choices, the seed, the count, and the prompt — and
    # authenticated with fal's own scheme.
    (submitted,) = fal.submissions()
    assert submitted == {"prompt": PROMPT, "image_size": "square_hd", "seed": 42, "num_images": 1}
    (submit,) = fal.made("POST", host="queue.fal.run")
    assert str(submit.url) == f"https://queue.fal.run/{T2I}"
    assert submit.headers["authorization"] == f"Key {FAL_KEY}"

    # The CDN download carries no credentials: the key goes to fal and nowhere else.
    (download,) = fal.downloads()
    assert "authorization" not in download.headers

    assert asset.asset_type == "image"
    assert asset.source == "ai_generated"
    assert asset.ai_model == T2I
    assert asset.ai_generation_type == "text_to_image"
    assert asset.ai_prompt == PROMPT
    assert json.loads(asset.ai_parameters) == {"image_size": "square_hd", "seed": 42, "num_images": 1}
    assert json.loads(asset.ai_source_assets) == []
    assert asset.ai_seed == 1234
    assert asset.ai_generated_at is not None
    assert asset.description == PROMPT
    assert asset.name == "A red fox curled up in fresh snow at golden hour, shallow depth of field"
    provenance = json.loads(asset.field_provenance)
    assert provenance["name"] == "ai" and provenance["description"] == "ai"

    body = library.get(f"/api/assets/{asset.id}").json()["data"]
    assert body["source"] == "ai_generated"
    assert body["thumb_url"]
    assert body["width"] == 400 and body["height"] == 400
    assert body["generation"]["model"] == T2I
    assert body["generation"]["kind"] == "text_to_image"
    assert body["generation"]["prompt"] == PROMPT
    assert body["generation"]["parameters"] == {"image_size": "square_hd", "seed": 42, "num_images": 1}
    assert body["generation"]["sources"] == []
    assert body["generation"]["seed"] == 1234
    assert body["generation"]["generated_at"]

    # One usage row: fal's billed units times fal's own price, so exact.
    (usage,) = _usage(session)
    assert usage.asset_id == asset.id
    assert usage.user_id == TEST_USER
    assert usage.kind == "image"
    assert usage.model == T2I
    assert usage.units == 1
    assert usage.unit_type == "megapixels"
    assert usage.cost == pytest.approx(0.025)
    assert usage.currency == "USD"
    assert usage.cost_estimated is False
    assert usage.external_ref == "fal-req-1"

    totals = library.get(f"/api/usage/assets/{asset.id}").json()["data"]
    assert totals["cost"] == pytest.approx(0.025)
    assert totals["estimated"] is False


def test_the_prompt_makes_the_generation_findable(library, session, monkeypatch, ready, fal):
    _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    hits = library.get("/api/assets", params={"q": "fox"}).json()
    assert hits["total"] == 1
    listing = library.get("/api/assets", params={"source": "ai_generated"}).json()
    assert listing["total"] == 1


def test_a_long_prompt_names_the_asset_at_a_word_boundary(library, session, monkeypatch, ready, fal):
    prompt = "A lighthouse " + "on a rocky headland under a bruised sky " * 6
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": prompt})

    (asset,) = _generated(session)
    assert len(asset.name) <= 80
    assert asset.name.endswith("…")
    assert prompt.startswith(asset.name[:-1])
    assert asset.description == prompt.strip()
    assert _activity(library, job_id)["asset_name"] == asset.name


def test_several_outputs_become_several_assets_in_order(library, session, monkeypatch, ready, fal):
    fal.result_body = {
        "images": [
            {"url": f"{CDN}/out-0.png", "content_type": "image/png"},
            {"url": f"{CDN}/out-1.jpg", "content_type": "image/jpeg"},
        ],
        "seed": 7,
    }
    fal.files[f"{CDN}/out-1.jpg"] = FIXTURES / "sample_image.jpg"
    fal.result_headers = {"x-fal-billable-units": "2", "x-fal-request-id": "fal-req-1"}

    job_id = _generate(
        library, session, monkeypatch,
        {"model_id": ready[T2I].id, "prompt": PROMPT, "params": {"num_outputs": 2}},
    )

    job = _activity(library, job_id)
    assert job["detail"] == "2 images"
    assert fal.submissions()[0]["num_images"] == 2
    assets = {a.id: a for a in _generated(session)}
    assert len(assets) == 2
    first, second = job["result_asset_ids"]
    assert job["result_asset_id"] == first
    assert assets[first].file_format == "png"
    assert assets[second].file_format == "jpg"

    # One request, one bill — attached to the first output.
    (usage,) = _usage(session)
    assert usage.asset_id == first
    assert usage.units == 2
    assert usage.cost == pytest.approx(0.05)


def test_an_embedding_provider_gets_the_new_asset_embedded(library, session, monkeypatch, ready, fal):
    set_setting(session, TEST_USER, EMBEDDING_PROVIDER, PROVIDER_OLLAMA)
    _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    (asset,) = _generated(session)
    embeds = session.exec(
        select(EnrichmentJob).where(EnrichmentJob.asset_id == asset.id, EnrichmentJob.kind == KIND_EMBED)
    ).all()
    assert len(embeds) == 1


# ─── image → image ───────────────────────────────────────────────────────────


def test_image_to_image_sends_every_base_as_a_data_uri_in_order(library, session, monkeypatch, ready, fal):
    fal.price = (0.039, "image")
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    cutout = upload(library, "sample_image.png", "image/png")

    job_id = _generate(
        library, session, monkeypatch,
        {
            "model_id": ready[I2I].id,
            "prompt": "Put the subject of the second picture into the first",
            "base_asset_ids": [photo["id"], cutout["id"]],
            "params": {"aspect_ratio": "1:1"},
        },
    )
    assert _activity(library, job_id)["status"] == "done"

    (submitted,) = fal.submissions()
    assert set(submitted) == {"prompt", "aspect_ratio", "num_images", "image_urls"}
    first, second = submitted["image_urls"]
    # JPEG for the opaque photo; PNG for the one with real transparency, which an edit
    # model needs to see.
    header, image = _decoded(first)
    assert header == "data:image/jpeg;base64"
    assert image.size == (800, 600)
    header, image = _decoded(second)
    assert header == "data:image/png;base64"
    assert image.mode == "RGBA"

    (asset,) = _generated(session)
    assert asset.ai_generation_type == "image_to_image"
    assert json.loads(asset.ai_parameters) == {"aspect_ratio": "1:1", "num_images": 1}
    assert json.loads(asset.ai_source_assets) == [
        {"asset_id": photo["id"], "role": "base", "name": "sample_image"},
        {"asset_id": cutout["id"], "role": "base", "name": "sample_image"},
    ]
    # A base is not a parent: no inherited attribution, no Clip tab.
    assert asset.parent_asset_id is None

    # Priced per image, because fal's pricing API says this endpoint is.
    (usage,) = _usage(session)
    assert usage.unit_type == "images"
    assert usage.cost == pytest.approx(0.039)
    assert usage.cost_estimated is False


def _upload_bytes(library, name, data, media_type):
    response = library.post("/api/assets", files=[("files", (name, data, media_type))])
    assert response.status_code in (200, 201), response.text
    return response.json()["created"][0]


def test_a_base_is_turned_upright_and_capped_before_it_is_sent(library, session, monkeypatch, ready, fal):
    # A phone photo: stored landscape, tagged "rotate 90° clockwise to view".
    picture = Image.new("RGB", (3000, 1000), (200, 40, 40))
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", exif=exif)
    phone = _upload_bytes(library, "phone.jpg", buffer.getvalue(), "image/jpeg")

    _generate(
        library, session, monkeypatch,
        {"model_id": ready[I2I].id, "prompt": "Make it night", "base_asset_ids": [phone["id"]]},
    )

    (submitted,) = fal.submissions()
    _header, sent = _decoded(submitted["image_urls"][0])
    # Upright (portrait), and the long edge brought down to the 2048 px cap.
    assert sent.size == (683, 2048)


def test_a_base_still_too_large_after_resizing_is_refused(library, session, monkeypatch, ready, fal):
    monkeypatch.setattr(settings, "generation_max_data_uri_mb", 0)
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    job_id = _generate(
        library, session, monkeypatch,
        {"model_id": ready[I2I].id, "prompt": "Make it night", "base_asset_ids": [photo["id"]]},
    )

    job = _activity(library, job_id)
    assert job["status"] == "error"
    assert job["error_message"].startswith("A base image is larger than the 0 MB limit")
    assert fal.submissions() == []


# ─── image → video ───────────────────────────────────────────────────────────


@needs_ffmpeg
def test_image_to_video_puts_the_end_frame_in_the_models_field(library, session, monkeypatch, ready, fal):
    # A Deepgram key is set, which would transcribe an uploaded video on arrival. A
    # generated one is not transcribed: most are silent, and each would be billed.
    set_setting(session, TEST_USER, DEEPGRAM_API_KEY, "dg-key")
    start = upload(library, "sample_image.jpg", "image/jpeg")
    end = upload(library, "attributed_image.jpg", "image/jpeg")
    fal.result_body = {"video": {"url": f"{CDN}/clip.mp4", "content_type": "video/mp4"}}
    fal.files[f"{CDN}/clip.mp4"] = FIXTURES / "sample_video.mp4"
    fal.result_headers = {"x-fal-billable-units": "5", "x-fal-request-id": "fal-req-1"}
    fal.price = (0.07, "second")

    job_id = _generate(
        library, session, monkeypatch,
        {
            "model_id": ready[I2V].id,
            "prompt": "The fox wakes and runs off",
            "base_asset_ids": [start["id"]],
            "end_frame_asset_id": end["id"],
            "params": {"duration": "5", "negative_prompt": "blur"},
        },
    )
    job = _activity(library, job_id)
    assert job["status"] == "done", job["error_message"]
    assert job["detail"] == "1 video"

    (submitted,) = fal.submissions()
    assert submitted["image_url"].startswith("data:image/jpeg;base64,")
    assert submitted["tail_image_url"].startswith("data:image/jpeg;base64,")
    assert submitted["duration"] == "5"
    assert submitted["negative_prompt"] == "blur"
    # A video model makes one, and is not sent a count it has no field for.
    assert "num_images" not in submitted
    # Polled at the app path fal's queue uses, not the full endpoint id.
    assert fal.made("GET", suffix="/status")[0].url.path == "/fal-ai/kling-video/requests/req-1/status"

    (asset,) = _generated(session)
    assert asset.asset_type == "video"
    assert asset.duration_seconds == pytest.approx(2.0, abs=0.2)
    assert asset.ai_seed is None
    assert json.loads(asset.ai_source_assets) == [
        {"asset_id": start["id"], "role": "base", "name": "sample_image"},
        {"asset_id": end["id"], "role": "end_frame", "name": "attributed_image"},
    ]

    transcribes = session.exec(
        select(EnrichmentJob).where(EnrichmentJob.asset_id == asset.id, EnrichmentJob.kind == KIND_TRANSCRIBE)
    ).all()
    assert transcribes == []

    (usage,) = _usage(session)
    assert usage.kind == "video"
    # Not "seconds", which the totals already count as audio transcribed.
    assert usage.unit_type == "generated_seconds"
    assert usage.units == 5
    assert usage.cost == pytest.approx(0.35)


def test_a_base_deleted_while_queued_fails_readably(library, session, monkeypatch, ready, fal):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    job_id = _start(
        library,
        {"model_id": ready[I2I].id, "prompt": "Make it night", "base_asset_ids": [photo["id"]]},
    )
    assert library.delete(f"/api/assets/{photo['id']}").status_code == 204

    job = run_job(session, monkeypatch, job_id)
    assert job.status == "error"
    assert job.error_message == "A base image was deleted before this could run"
    assert fal.submissions() == []


# ─── restarts ────────────────────────────────────────────────────────────────


def test_a_restart_resumes_the_stored_request_instead_of_submitting_again(
    library, session, monkeypatch, ready, fal
):
    fal.statuses = [{"status": "IN_QUEUE", "queue_position": 2}, ProcessDied]
    job_id = _start(library, {"model_id": ready[T2I].id, "prompt": PROMPT})

    with pytest.raises(ProcessDied):
        run_job(session, monkeypatch, job_id)

    # What the killed process left: a running row whose request ids were committed
    # before the first poll.
    session.expire_all()
    job = session.get(EnrichmentJob, job_id)
    assert job.status == "processing"
    stored = json.loads(job.payload)
    assert stored["fal_request"]["request_id"] == "req-1"
    assert stored["fal_request"]["cancel_url"].endswith("/requests/req-1/cancel")

    queue = enrichment_jobs.generation_queue()
    monkeypatch.setattr(queue, "engine", session.get_bind())
    queue.recover_pending()
    session.expire_all()
    assert session.get(EnrichmentJob, job_id).status == "queued"

    fal.statuses = [{"status": "COMPLETED"}]
    job = run_job(session, monkeypatch, job_id)

    assert job.status == "done", job.error_message
    assert len(fal.submissions()) == 1
    assert len(_generated(session)) == 1


def test_a_restart_after_saving_does_not_save_or_bill_twice(library, session, monkeypatch, ready, fal):
    # Dies looking up the price: after the asset is saved, before the cost is recorded.
    fal.pricing_raises = ProcessDied
    job_id = _start(library, {"model_id": ready[T2I].id, "prompt": PROMPT})
    with pytest.raises(ProcessDied):
        run_job(session, monkeypatch, job_id)

    session.expire_all()
    stored = json.loads(session.get(EnrichmentJob, job_id).payload)
    assert len(stored["created_asset_ids"]) == 1
    assert stored["usage_recorded"] is False

    fal.pricing_raises = None
    job = run_job(session, monkeypatch, job_id)

    assert job.status == "done", job.error_message
    assert len(_generated(session)) == 1
    assert len(fal.submissions()) == 1
    assert len(fal.downloads()) == 1
    # The result was kept with the job, so the rerun did not even ask for it again.
    assert len(fal.made("GET", host="queue.fal.run", suffix="/req-1")) == 1
    assert len(_usage(session)) == 1
    assert json.loads(job.payload)["created_asset_ids"] == stored["created_asset_ids"]


def test_running_a_finished_generation_again_changes_nothing(library, session, monkeypatch, ready, fal):
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})
    calls = len(fal.calls)

    job = session.get(EnrichmentJob, job_id)
    job.status = "queued"
    session.add(job)
    session.commit()
    job = run_job(session, monkeypatch, job_id)

    assert job.status == "done"
    assert len(_generated(session)) == 1
    assert len(_usage(session)) == 1
    assert len(fal.calls) == calls


# ─── stopping ────────────────────────────────────────────────────────────────


def test_a_user_cancel_tells_fal_to_stop(library, session, monkeypatch, ready, fal):
    fal.statuses = [{"status": "IN_PROGRESS"}]
    job_id = _start(library, {"model_id": ready[T2I].id, "prompt": PROMPT})
    fal.clock.on_sleep = lambda: library.delete(f"/api/activity/enrichment/{job_id}")

    job = run_job(session, monkeypatch, job_id)

    assert job.status == "cancelled"
    (cancel,) = fal.cancels()
    assert str(cancel.url) == f"https://queue.fal.run/fal-ai/flux/requests/req-1/cancel"
    assert cancel.headers["authorization"] == f"Key {FAL_KEY}"
    assert _generated(session) == []
    assert _usage(session) == []


def test_the_deadline_cancels_at_fal_and_says_so(library, session, monkeypatch, ready, fal):
    monkeypatch.setattr(settings, "generation_poll_seconds", 60)
    fal.statuses = [{"status": "IN_QUEUE", "queue_position": 0}, {"status": "IN_PROGRESS"}]
    job_id = _start(library, {"model_id": ready[T2I].id, "prompt": PROMPT})

    job = run_job(session, monkeypatch, job_id)

    assert job.status == "error"
    assert job.error_message == "fal.ai did not finish within 30 minutes"
    assert len(fal.cancels()) == 1
    # Waited the whole thirty minutes on the fake clock, in steps of the poll interval.
    assert sum(fal.clock.slept) == pytest.approx(30 * 60)
    assert set(fal.clock.slept) == {60}


def test_a_status_that_fails_once_is_polled_again(library, session, monkeypatch, ready, fal):
    fal.statuses = [httpx.Response(502, json={"detail": "Bad gateway"}), {"status": "COMPLETED"}]
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})
    assert _activity(library, job_id)["status"] == "done"


def test_completed_with_an_error_is_a_failure(library, session, monkeypatch, ready, fal):
    fal.statuses = [{"status": "COMPLETED", "error": "Content policy violation", "error_type": "content_policy"}]
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    job = _activity(library, job_id)
    assert job["status"] == "error"
    assert job["error_message"] == "fal.ai could not generate this: Content policy violation"
    # Checked before fetching a result that is not there.
    assert fal.made("GET", host="queue.fal.run", suffix="/req-1") == []
    assert _generated(session) == []


def test_a_rejected_key_is_named_as_one(library, session, monkeypatch, ready, fal):
    fal.submit_response = httpx.Response(401, json={"detail": "Invalid key"})
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    job = _activity(library, job_id)
    assert job["status"] == "error"
    assert job["error_message"] == "fal.ai rejected the API key — check it in Settings"


def test_a_validation_error_names_the_field(library, session, monkeypatch, ready, fal):
    fal.submit_response = httpx.Response(
        422,
        json={"detail": [{"loc": ["body", "image_urls"], "msg": "field required", "type": "missing"}]},
    )
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    assert _activity(library, job_id)["error_message"] == (
        "fal.ai refused the request: image_urls: field required"
    )


def test_an_error_on_the_result_fetch_is_reported(library, session, monkeypatch, ready, fal):
    fal.result_response = httpx.Response(
        422, json={"detail": [{"loc": ["body", "prompt"], "msg": "too long", "type": "value_error"}]}
    )
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})
    assert _activity(library, job_id)["error_message"] == "fal.ai refused the request: prompt: too long"


# ─── the download ────────────────────────────────────────────────────────────


def test_a_result_url_pointing_inward_is_not_fetched(library, session, monkeypatch, ready, fal):
    fal.result_body = {"images": [{"url": "https://metadata.internal/latest/x.png"}]}
    fal.addresses["metadata.internal"] = "169.254.169.254"

    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    job = _activity(library, job_id)
    assert job["status"] == "error"
    assert job["error_message"].startswith("The generated file is at an address this app will not fetch")
    assert fal.downloads() == []


def test_a_redirect_inward_is_not_followed(library, session, monkeypatch, ready, fal):
    fal.files[f"{CDN}/out-0.png"] = httpx.Response(302, headers={"location": "https://10.0.0.5/x.png"})

    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    assert "will not fetch" in _activity(library, job_id)["error_message"]
    assert [r.url.host for r in fal.downloads()] == ["v3.fal.media"]


def test_a_public_redirect_is_followed(library, session, monkeypatch, ready, fal):
    fal.files[f"{CDN}/out-0.png"] = httpx.Response(302, headers={"location": "/files/moved.png"})
    fal.files[f"{CDN}/moved.png"] = FIXTURES / "sample_image.png"

    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})
    assert _activity(library, job_id)["status"] == "done"


def test_redirects_are_followed_at_most_three_times(library, session, monkeypatch, ready, fal):
    for hop in range(4):
        fal.files[f"{CDN}/hop-{hop}.png"] = httpx.Response(
            302, headers={"location": f"{CDN}/hop-{hop + 1}.png"}
        )
    fal.files[f"{CDN}/hop-4.png"] = FIXTURES / "sample_image.png"
    fal.result_body = {"images": [{"url": f"{CDN}/hop-0.png"}]}

    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    assert _activity(library, job_id)["error_message"] == (
        "Could not download the generated file (too many redirects)"
    )
    assert len(fal.downloads()) == 4


def test_an_inline_result_is_saved_without_a_download(library, session, monkeypatch, ready, fal):
    encoded = base64.b64encode((FIXTURES / "sample_image.jpg").read_bytes()).decode()
    fal.result_body = {"images": [{"url": f"data:image/jpeg;base64,{encoded}"}]}

    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    assert _activity(library, job_id)["status"] == "done"
    assert fal.downloads() == []
    (asset,) = _generated(session)
    assert asset.file_format == "jpg"


def test_an_oversized_file_is_refused_from_its_content_length(library, session, monkeypatch, ready, fal):
    monkeypatch.setattr(settings, "generation_max_download_mb", 1)
    fal.files[f"{CDN}/out-0.png"] = httpx.Response(
        200, content=b"\0" * (2 * 1024 * 1024), headers={"content-type": "image/png"}
    )
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    job = _activity(library, job_id)
    assert job["error_message"] == "The generated file is larger than the 1 MB limit"
    assert _generated(session) == []


def test_an_oversized_file_without_a_length_is_stopped_while_streaming(
    library, session, monkeypatch, ready, fal
):
    monkeypatch.setattr(settings, "generation_max_download_mb", 1)
    fal.files[f"{CDN}/out-0.png"] = httpx.Response(
        200,
        stream=httpx.ByteStream(b"\0" * (2 * 1024 * 1024 + 1)),
        headers={"content-type": "image/png"},
    )
    job_id = _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    assert _activity(library, job_id)["error_message"] == "The generated file is larger than the 1 MB limit"
    assert _generated(session) == []


def test_the_extension_comes_from_the_content_type_when_the_url_has_none(
    library, session, monkeypatch, ready, fal
):
    fal.result_body = {"images": [{"url": f"{CDN}/abc123"}]}
    fal.files[f"{CDN}/abc123"] = httpx.Response(
        200, content=(FIXTURES / "sample_image.jpg").read_bytes(), headers={"content-type": "image/jpeg"}
    )
    _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    (asset,) = _generated(session)
    assert asset.file_format == "jpg"


# ─── the cost ────────────────────────────────────────────────────────────────


def test_without_the_pricing_api_the_catalogue_price_is_an_estimate(
    library, session, monkeypatch, ready, fal
):
    fal.pricing_status = 500
    fal.result_headers = {"x-fal-billable-units": "2", "x-fal-request-id": "fal-req-1"}
    _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    (usage,) = _usage(session)
    assert usage.units == 2
    assert usage.unit_type == "megapixels"
    assert usage.cost == pytest.approx(0.05)  # 2 × the catalogue's 0.025
    assert usage.cost_estimated is True


@needs_ffmpeg
def test_without_the_header_the_units_are_worked_out_from_the_files(
    library, session, monkeypatch, ready, fal
):
    fal.result_headers = {"x-fal-request-id": "fal-req-9"}
    fal.result_body = {"images": [{"url": f"{CDN}/big.jpg"}]}
    fal.files[f"{CDN}/big.jpg"] = FIXTURES / "sample_image.jpg"  # 800×600: one megapixel
    _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    (usage,) = _usage(session)
    assert usage.units == 1
    assert usage.unit_type == "megapixels"
    assert usage.cost == pytest.approx(0.025)
    assert usage.cost_estimated is True
    assert usage.external_ref == "fal-req-9"


def test_with_no_price_anywhere_the_cost_is_left_unknown(library, session, monkeypatch, ready, fal):
    fal.pricing_status = 404
    ready[T2I].unit_price = None
    session.add(ready[T2I])
    session.commit()
    _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    (usage,) = _usage(session)
    assert usage.units == 1
    assert usage.cost is None
    assert usage.currency is None
    assert usage.cost_estimated is None


def test_a_total_mixing_a_bill_with_an_estimate_is_an_estimate(library, session, monkeypatch, ready, fal):
    _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})
    assert library.get("/api/usage").json()["data"]["totals"]["estimated"] is False

    session.add(UsageEvent(
        user_id=TEST_USER, kind="ai", provider="anthropic", model="claude", units=100,
        unit_type="tokens", cost=0.01, currency="USD", cost_estimated=True,
    ))
    session.commit()
    assert library.get("/api/usage").json()["data"]["totals"]["estimated"] is True


def test_the_price_is_asked_for_with_the_users_key_and_cached(library, session, monkeypatch, ready, fal):
    for _ in range(2):
        _generate(library, session, monkeypatch, {"model_id": ready[T2I].id, "prompt": PROMPT})

    (lookup,) = fal.made("GET", host="api.fal.ai")
    assert lookup.url.params["endpoint_id"] == T2I
    assert lookup.headers["authorization"] == f"Key {FAL_KEY}"
    assert len(_usage(session)) == 2
