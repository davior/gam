"""`POST /api/generate` and `/regenerate` — what is refused before anything is queued.

The order of the checks is part of the contract (docs/m8-ai-generation.md), so several
tests send a request wrong in two ways and assert which one is reported.
"""

import json

import pytest
from sqlmodel import select

from app.jobs import enrichment as enrichment_jobs
from app.models.asset import Asset
from app.models.job import KIND_GENERATE, EnrichmentJob
from tests.fal_stub import (  # noqa: F401 - `fal` is a fixture
    I2I,
    I2V,
    T2I,
    TEST_USER,
    VEO,
    fal,
    seed_models,
    set_fal_key,
    upload,
)

PROMPT = "A lighthouse on a rocky headland under a bruised sky"


@pytest.fixture
def models(session):
    return seed_models(session)


@pytest.fixture
def keyed(session, models):
    set_fal_key(session)
    return models


def _post(library, body):
    return library.post("/api/generate", json=body)


def _refused(response, status, code):
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"]
    return detail["message"]


def _request_of(session, job_id):
    return json.loads(session.get(EnrichmentJob, job_id).payload)["request"]


def _someone_elses_image(session):
    asset = Asset(
        user_id="someone-else", name="Their photo", asset_type="image",
        storage_key="someone-else/photo.jpg", file_format="jpg",
    )
    session.add(asset)
    session.commit()
    return asset


# ─── what a valid request queues ─────────────────────────────────────────────


def test_a_generation_is_queued_as_a_job(library, session, keyed):
    response = _post(library, {
        "model_id": keyed[T2I].id,
        "prompt": f"  {PROMPT}  ",
        "params": {"image_size": "landscape_16_9", "seed": 7, "num_outputs": 3},
    })

    assert response.status_code == 202, response.text
    job = response.json()["data"]
    assert job["kind"] == "enrichment"
    assert job["action"] == KIND_GENERATE
    assert job["status"] == "queued"
    assert job["asset_id"] is None
    assert job["asset_name"] == PROMPT
    assert job["model"] == T2I
    assert job["result_asset_id"] is None
    assert job["result_asset_ids"] == []

    request = _request_of(session, job["id"])
    assert request["endpoint_id"] == T2I
    assert request["kind"] == "text_to_image"
    assert request["prompt"] == PROMPT
    assert request["parameters"] == {"image_size": "landscape_16_9", "seed": 7, "num_images": 3}
    assert request["sources"] == []


def test_catalogue_defaults_are_part_of_the_stored_parameters(library, session, keyed):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    response = _post(library, {
        "model_id": keyed[VEO].id,
        "prompt": PROMPT,
        "base_asset_ids": [photo["id"]],
        "params": {"duration": "8s", "resolution": "1080p"},
    })
    assert response.status_code == 202, response.text

    # Veo's `generate_audio: false` default is recorded, so a regeneration after an
    # admin changes it still makes what this one made.
    parameters = _request_of(session, response.json()["data"]["id"])["parameters"]
    assert parameters == {"generate_audio": False, "duration": "8s", "resolution": "1080p"}


def test_a_user_choice_overrides_a_catalogue_default(library, session, keyed):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    response = _post(library, {
        "model_id": keyed[VEO].id,
        "prompt": PROMPT,
        "base_asset_ids": [photo["id"]],
        "params": {"generate_audio": True},
    })
    assert _request_of(session, response.json()["data"]["id"])["parameters"]["generate_audio"] is True


def test_it_runs_on_the_generation_queue_not_the_enrichment_one(library, session, keyed):
    job_id = _post(library, {"model_id": keyed[T2I].id, "prompt": PROMPT}).json()["data"]["id"]

    assert job_id in list(enrichment_jobs.generation_queue()._queue.queue)
    assert job_id not in list(enrichment_jobs.queue()._queue.queue)


def test_cancelling_reaches_the_generation_queue(library, session, keyed):
    job_id = _post(library, {"model_id": keyed[T2I].id, "prompt": PROMPT}).json()["data"]["id"]

    response = library.delete(f"/api/activity/enrichment/{job_id}")

    assert response.json()["data"]["status"] == "cancelled"
    assert enrichment_jobs.generation_queue().is_cancelled(job_id)
    assert not enrichment_jobs.queue().is_cancelled(job_id)


# ─── the checks, in order ────────────────────────────────────────────────────


def test_no_key_is_reported_first(library, models):
    message = _refused(_post(library, {"model_id": "no-such-model", "prompt": ""}), 400, "fal_key_missing")
    assert "Settings" in message


def test_an_unknown_model(library, keyed):
    _refused(_post(library, {"model_id": "no-such-model", "prompt": ""}), 404, "model_not_found")


def test_a_switched_off_model(library, session, keyed):
    keyed[T2I].is_active = False
    session.add(keyed[T2I])
    session.commit()
    _refused(_post(library, {"model_id": keyed[T2I].id, "prompt": ""}), 409, "model_inactive")


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 4001])
def test_the_prompt_must_be_there_and_not_too_long(library, keyed, prompt):
    _refused(_post(library, {"model_id": keyed[T2I].id, "prompt": prompt}), 422, "invalid_generation")


def test_a_prompt_of_exactly_the_limit_is_accepted(library, keyed):
    assert _post(library, {"model_id": keyed[T2I].id, "prompt": "x" * 4000}).status_code == 202


def test_text_to_image_takes_no_base(library, keyed):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    message = _refused(
        _post(library, {"model_id": keyed[T2I].id, "prompt": PROMPT, "base_asset_ids": [photo["id"]]}),
        422, "invalid_generation",
    )
    assert "takes no base image" in message


@pytest.mark.parametrize("count", [0, 4])
def test_image_to_image_takes_between_one_and_its_maximum(library, keyed, count):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    message = _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": PROMPT, "base_asset_ids": [photo["id"]] * count}),
        422, "invalid_generation",
    )
    assert "between 1 and 3" in message


@pytest.mark.parametrize("count", [0, 2])
def test_image_to_video_takes_exactly_one_start_image(library, keyed, count):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    _refused(
        _post(library, {"model_id": keyed[I2V].id, "prompt": PROMPT, "base_asset_ids": [photo["id"]] * count}),
        422, "invalid_generation",
    )


def test_an_end_frame_needs_a_model_with_somewhere_to_put_it(library, keyed):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    message = _refused(
        _post(library, {
            "model_id": keyed[VEO].id, "prompt": PROMPT,
            "base_asset_ids": [photo["id"]], "end_frame_asset_id": photo["id"],
        }),
        422, "invalid_generation",
    )
    assert "end frame" in message


@pytest.mark.parametrize(
    "model, params, fragment",
    [
        (T2I, {"image_size": "enormous"}, "square_hd, landscape_16_9"),
        # The declared value is the string "5"; the number 5 is a different request.
        (I2V, {"duration": 5}, "5, 10"),
        (T2I, {"aspect_ratio": "1:1"}, "does not take an aspect ratio"),
        (I2V, {"resolution": "720p"}, "does not take a resolution"),
    ],
)
def test_a_choice_must_be_one_the_model_declares(library, keyed, model, params, fragment):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    bases = [photo["id"]] if model != T2I else []
    message = _refused(
        _post(library, {"model_id": keyed[model].id, "prompt": PROMPT, "base_asset_ids": bases, "params": params}),
        422, "invalid_generation",
    )
    assert fragment in message


@pytest.mark.parametrize(
    "model, params",
    [
        (I2V, {"seed": 1}),                    # Kling 2.5 reports and takes no seed
        (T2I, {"negative_prompt": "blur"}),    # FLUX has no negative prompt
        (T2I, {"generate_audio": True}),       # nor any audio
    ],
)
def test_a_feature_the_model_lacks_is_refused(library, keyed, model, params):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    bases = [photo["id"]] if model != T2I else []
    _refused(
        _post(library, {"model_id": keyed[model].id, "prompt": PROMPT, "base_asset_ids": bases, "params": params}),
        422, "invalid_generation",
    )


def test_an_empty_negative_prompt_is_not_a_request_for_one(library, keyed):
    response = _post(library, {"model_id": keyed[T2I].id, "prompt": PROMPT, "params": {"negative_prompt": ""}})
    assert response.status_code == 202


@pytest.mark.parametrize("model, count", [(T2I, 0), (T2I, 5), (I2V, 2)])
def test_the_number_of_outputs_is_bounded(library, keyed, model, count):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    bases = [photo["id"]] if model != T2I else []
    _refused(
        _post(library, {"model_id": keyed[model].id, "prompt": PROMPT, "base_asset_ids": bases, "params": {"num_outputs": count}}),
        422, "invalid_generation",
    )


def test_someone_elses_base_is_not_found(library, session, keyed):
    theirs = _someone_elses_image(session)
    _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": PROMPT, "base_asset_ids": [theirs.id]}),
        404, "asset_not_found",
    )


def test_a_bad_request_is_reported_before_an_unknown_base(library, keyed):
    _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": "", "base_asset_ids": ["nope"]}),
        422, "invalid_generation",
    )


def test_ownership_is_checked_before_usability(library, session, keyed):
    video = upload(library, "sample_video.mp4", "video/mp4")
    theirs = _someone_elses_image(session)
    _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": PROMPT, "base_asset_ids": [video["id"], theirs.id]}),
        404, "asset_not_found",
    )


def test_a_base_must_be_an_image(library, keyed):
    video = upload(library, "sample_video.mp4", "video/mp4")
    message = _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": PROMPT, "base_asset_ids": [video["id"]]}),
        422, "invalid_base",
    )
    assert "not an image" in message


def test_a_base_must_own_a_file(library, session, keyed):
    clip = Asset(user_id=TEST_USER, name="A clip", asset_type="image", source="clip")
    session.add(clip)
    session.commit()
    _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": PROMPT, "base_asset_ids": [clip.id]}),
        422, "invalid_base",
    )


def test_a_base_must_be_a_format_pillow_reads(library, keyed):
    response = library.post(
        "/api/assets",
        files=[("files", ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"))],
    )
    svg = response.json()["created"][0]
    message = _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": PROMPT, "base_asset_ids": [svg["id"]]}),
        422, "invalid_base",
    )
    assert "SVG" in message


def test_a_base_must_actually_be_readable(library, keyed):
    response = library.post(
        "/api/assets", files=[("files", ("broken.png", b"not a png at all", "image/png"))]
    )
    broken = response.json()["created"][0]
    message = _refused(
        _post(library, {"model_id": keyed[I2I].id, "prompt": PROMPT, "base_asset_ids": [broken["id"]]}),
        422, "invalid_base",
    )
    assert "could not be read" in message


def test_the_end_frame_is_checked_like_a_base(library, session, keyed):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    theirs = _someone_elses_image(session)
    _refused(
        _post(library, {
            "model_id": keyed[I2V].id, "prompt": PROMPT,
            "base_asset_ids": [photo["id"]], "end_frame_asset_id": theirs.id,
        }),
        404, "asset_not_found",
    )


def test_generating_requires_authentication(client):
    assert client.post("/api/generate", json={}).status_code == 401


# ─── regenerate ──────────────────────────────────────────────────────────────


@pytest.fixture
def made(library, session, keyed):
    """A generated image→image asset, as the job would have left it."""
    first = upload(library, "sample_image.jpg", "image/jpeg")
    second = upload(library, "sample_image.png", "image/png")
    asset = Asset(
        user_id=TEST_USER,
        name=PROMPT,
        description=PROMPT,
        asset_type="image",
        source="ai_generated",
        storage_key=f"{TEST_USER}/made.png",
        ai_model=I2I,
        ai_generation_type="image_to_image",
        ai_prompt=PROMPT,
        ai_parameters=json.dumps({"aspect_ratio": "1:1", "num_images": 1, "seed": 42}),
        ai_source_assets=json.dumps([
            {"asset_id": first["id"], "role": "base", "name": "First"},
            {"asset_id": second["id"], "role": "base", "name": "Second"},
        ]),
        ai_seed=1234,
    )
    session.add(asset)
    session.commit()
    return {"asset": asset, "bases": [first["id"], second["id"]]}


def _regenerate(library, asset_id, body=None):
    return library.post(f"/api/generate/{asset_id}/regenerate", json=body)


def test_regenerate_resends_the_stored_request(library, session, made):
    response = _regenerate(library, made["asset"].id)
    assert response.status_code == 202, response.text
    job = response.json()["data"]
    assert job["action"] == KIND_GENERATE
    assert job["model"] == I2I

    request = _request_of(session, job["id"])
    assert request["endpoint_id"] == I2I
    assert request["prompt"] == PROMPT
    assert request["image_field"] == "image_urls" and request["image_field_is_list"] is True
    assert [s["asset_id"] for s in request["sources"]] == made["bases"]
    assert {s["role"] for s in request["sources"]} == {"base"}
    assert request["regenerated_from"] == made["asset"].id
    # Everything as stored, except the seed: a regeneration that reproduced the same
    # picture would not be one.
    assert request["parameters"] == {"aspect_ratio": "1:1", "num_images": 1}


def test_regenerate_with_a_new_prompt(library, session, made):
    response = _regenerate(library, made["asset"].id, {"prompt": "The same, at night"})
    assert _request_of(session, response.json()["data"]["id"])["prompt"] == "The same, at night"


def test_regenerate_can_reuse_the_seed_fal_reported(library, session, made):
    response = _regenerate(library, made["asset"].id, {"reuse_seed": True})
    assert _request_of(session, response.json()["data"]["id"])["parameters"]["seed"] == 1234


def test_reuse_seed_does_nothing_for_a_model_without_seeds(library, session, keyed, made):
    options = json.loads(keyed[I2I].options)
    options["supports_seed"] = False
    keyed[I2I].options = json.dumps(options)
    session.add(keyed[I2I])
    session.commit()

    response = _regenerate(library, made["asset"].id, {"reuse_seed": True})
    assert "seed" not in _request_of(session, response.json()["data"]["id"])["parameters"]


def test_regenerate_names_the_deleted_base(library, session, made):
    assert library.delete(f"/api/assets/{made['bases'][1]}").status_code == 204
    message = _refused(_regenerate(library, made["asset"].id), 409, "source_asset_missing")
    assert "Second" in message


def test_regenerate_needs_the_model_still_active(library, session, keyed, made):
    keyed[I2I].is_active = False
    session.add(keyed[I2I])
    session.commit()
    _refused(_regenerate(library, made["asset"].id), 409, "model_unavailable")


def test_regenerate_needs_the_model_still_in_the_catalogue(library, session, keyed, made):
    session.delete(keyed[I2I])
    session.commit()
    message = _refused(_regenerate(library, made["asset"].id), 409, "model_unavailable")
    assert I2I in message


def test_only_a_generated_asset_can_be_regenerated(library, keyed):
    photo = upload(library, "sample_image.jpg", "image/jpeg")
    _refused(_regenerate(library, photo["id"]), 409, "not_generated")


def test_someone_elses_asset_cannot_be_regenerated(library, session, keyed):
    theirs = _someone_elses_image(session)
    theirs.ai_model = I2I
    theirs.ai_generation_type = "image_to_image"
    session.add(theirs)
    session.commit()
    assert _regenerate(library, theirs.id).status_code == 404


def test_regenerate_without_a_key(library, session, made):
    from app.settings_store import FAL_API_KEY, clear_setting

    clear_setting(session, TEST_USER, FAL_API_KEY)
    _refused(_regenerate(library, made["asset"].id), 400, "fal_key_missing")


def test_a_regenerated_job_runs_end_to_end(library, session, monkeypatch, made, fal):
    from tests.fal_stub import run_job

    job_id = _regenerate(library, made["asset"].id, {"reuse_seed": True}).json()["data"]["id"]
    job = run_job(session, monkeypatch, job_id)
    assert job.status == "done", job.error_message

    (submitted,) = fal.submissions()
    assert submitted["seed"] == 1234
    assert len(submitted["image_urls"]) == 2
    made_again = session.exec(
        select(Asset).where(Asset.source == "ai_generated", Asset.id != made["asset"].id)
    ).one()
    assert made_again.ai_prompt == PROMPT
    assert [s["asset_id"] for s in json.loads(made_again.ai_source_assets)] == made["bases"]
