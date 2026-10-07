"""The model catalogue, who may change it, and the fal key.

Admins are named in config (`ADMIN_USERS`) because the Notes token carries no admin
claim. The signed-in test user is `user-under-test` / `tester`.
"""

import json

import pytest
from sqlmodel import select

from app.config import settings
from app.models.setting import UserSetting
from app.models.user import User
from app.settings_store import FAL_API_KEY, get_setting
from tests.fal_stub import I2I, I2V, T2I, TEST_USER, VEO, seed_models
from tests.test_auth import make_token

NEW = {
    "endpoint_id": "fal-ai/flux/schnell",
    "kind": "text_to_image",
    "label": "FLUX.1 [schnell]",
    "options": {"image_sizes": ["square_hd"], "supports_seed": True, "max_outputs": 4},
    "unit_price": 0.003,
    "price_unit": "megapixel",
    "price_currency": "USD",
}

VIDEO = {
    "endpoint_id": "fal-ai/minimax/hailuo-02/standard/image-to-video",
    "kind": "image_to_video",
    "label": "Hailuo 02",
    "image_field": "image_url",
    "max_images": 1,
    "end_image_field": "end_image_url",
    "options": {"durations": ["6", "10"], "resolutions": ["512P", "768P"]},
}


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_users", TEST_USER)


@pytest.fixture
def models(session):
    return seed_models(session)


def _refused(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    return response.json()["detail"]["message"]


# ─── reading ─────────────────────────────────────────────────────────────────


def test_everyone_can_read_the_catalogue_in_form_order(auth_client, models):
    body = auth_client.get("/api/generate/models").json()

    assert body["total"] == 4
    order = [(m["kind"], m["endpoint_id"]) for m in body["data"]]
    assert order == [
        ("image_to_image", I2I),
        ("image_to_video", I2V),
        ("image_to_video", VEO),
        ("text_to_image", T2I),
    ]


def test_a_row_reads_with_every_option_present(auth_client, models):
    body = auth_client.get("/api/generate/models").json()["data"]
    flux = next(m for m in body if m["endpoint_id"] == T2I)

    assert flux["options"] == {
        "aspect_ratios": [],
        "image_sizes": ["square_hd", "landscape_16_9"],
        "durations": [],
        "resolutions": [],
        "supports_seed": True,
        "supports_negative_prompt": False,
        "supports_audio": False,
        "max_outputs": 4,
    }
    assert flux["extra_params"] == {}
    assert flux["image_field"] is None and flux["max_images"] == 0
    assert flux["unit_price"] == 0.025 and flux["price_unit"] == "megapixel"
    assert flux["created_at"] and flux["updated_at"]

    veo = next(m for m in body if m["endpoint_id"] == VEO)
    assert veo["extra_params"] == {"generate_audio": False}


def test_inactive_rows_are_for_admins_only(auth_client, session, models, monkeypatch):
    models[T2I].is_active = False
    session.add(models[T2I])
    session.commit()

    plain = auth_client.get("/api/generate/models", params={"include_inactive": True}).json()
    assert T2I not in [m["endpoint_id"] for m in plain["data"]]

    monkeypatch.setattr(settings, "admin_users", TEST_USER)
    assert T2I not in [m["endpoint_id"] for m in auth_client.get("/api/generate/models").json()["data"]]
    everything = auth_client.get("/api/generate/models", params={"include_inactive": True}).json()
    assert T2I in [m["endpoint_id"] for m in everything["data"]]


def test_the_catalogue_requires_authentication(client):
    assert client.get("/api/generate/models").status_code == 401


# ─── who is an admin ─────────────────────────────────────────────────────────


def test_a_non_admin_cannot_change_the_catalogue(auth_client, models):
    message = _refused(auth_client.post("/api/generate/models", json=NEW), 403, "admin_required")
    assert message
    _refused(auth_client.patch(f"/api/generate/models/{models[T2I].id}", json={"label": "x"}), 403, "admin_required")
    _refused(auth_client.delete(f"/api/generate/models/{models[T2I].id}"), 403, "admin_required")


@pytest.mark.parametrize("configured", ["user-under-test", "TESTER", " someone , Tester "])
def test_admin_users_matches_an_id_or_a_username(auth_client, monkeypatch, configured):
    monkeypatch.setattr(settings, "admin_users", configured)
    assert auth_client.post("/api/generate/models", json=NEW).status_code == 201


def test_an_id_matches_exactly(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_users", "USER-UNDER-TEST")
    _refused(auth_client.post("/api/generate/models", json=NEW), 403, "admin_required")


def test_the_user_rows_own_flag_still_counts(auth_client, session):
    session.add(User(id=TEST_USER, username="tester", is_admin=True))
    session.commit()
    assert auth_client.post("/api/generate/models", json=NEW).status_code == 201


def test_me_reports_admin_from_config(client, monkeypatch):
    token = make_token(sub="notes-user-1", username="davior")
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/me", headers=headers).json()["data"]["is_admin"] is False

    monkeypatch.setattr(settings, "admin_users", "Davior")
    assert client.get("/api/me", headers=headers).json()["data"]["is_admin"] is True

    monkeypatch.setattr(settings, "admin_users", "notes-user-1")
    assert client.get("/api/me", headers=headers).json()["data"]["is_admin"] is True


# ─── creating ────────────────────────────────────────────────────────────────


def test_an_admin_adds_a_model(auth_client, session, admin):
    response = auth_client.post("/api/generate/models", json=NEW)

    assert response.status_code == 201, response.text
    body = response.json()["data"]
    assert body["endpoint_id"] == "fal-ai/flux/schnell"
    assert body["is_active"] is True
    assert body["note"] == ""
    assert body["options"]["image_sizes"] == ["square_hd"]
    assert body["options"]["max_outputs"] == 4
    assert body["options"]["durations"] == []
    assert body["unit_price"] == 0.003

    listed = [m["endpoint_id"] for m in auth_client.get("/api/generate/models").json()["data"]]
    assert "fal-ai/flux/schnell" in listed


def test_a_video_model_with_an_end_frame(auth_client, admin):
    response = auth_client.post("/api/generate/models", json=VIDEO)
    assert response.status_code == 201, response.text
    assert response.json()["data"]["end_image_field"] == "end_image_url"


def test_a_duplicate_endpoint_is_a_conflict(auth_client, admin, models):
    _refused(
        auth_client.post("/api/generate/models", json={**NEW, "endpoint_id": T2I}),
        409, "endpoint_exists",
    )


@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"kind": "text_to_video"}, "kind"),
        ({"label": "  "}, "label"),
        ({"endpoint_id": "fal-ai/../admin"}, "endpoint_id"),
        ({"endpoint_id": "flux"}, "endpoint_id"),
        ({"endpoint_id": None}, "endpoint_id"),
        ({"image_field": "image_url"}, "no image_field"),
        ({"max_images": 1}, "max_images must be 0"),
        ({"end_image_field": "tail_image_url"}, "end frame"),
        ({"options": "square"}, "options must be an object"),
        ({"options": {"image_size": ["square_hd"]}}, "no setting called"),
        ({"options": {"image_sizes": "square_hd"}}, "options.image_sizes"),
        ({"options": {"max_outputs": 5}}, "max_outputs"),
        ({"options": {"max_outputs": 0}}, "max_outputs"),
        ({"options": {"supports_seed": "yes"}}, "supports_seed"),
        ({"extra_params": ["generate_audio"]}, "extra_params must be an object"),
        ({"price_unit": "token"}, "price_unit"),
        ({"unit_price": -1}, "unit_price"),
    ],
)
def test_an_invalid_text_to_image_entry_is_refused(auth_client, admin, change, fragment):
    message = _refused(
        auth_client.post("/api/generate/models", json={**NEW, **change}), 422, "invalid_model_entry"
    )
    assert fragment in message


@pytest.mark.parametrize(
    "change, fragment",
    [
        ({"image_field": None}, "image_field"),
        ({"max_images": 0}, "at least one base image"),
        ({"max_images": 2}, "image_field_is_list"),
        ({"options": {"max_outputs": 2}}, "max_outputs must be 1"),
        ({"image_field": "image url"}, "image_field"),
    ],
)
def test_an_invalid_video_entry_is_refused(auth_client, admin, change, fragment):
    message = _refused(
        auth_client.post("/api/generate/models", json={**VIDEO, **change}), 422, "invalid_model_entry"
    )
    assert fragment in message


def test_an_image_to_image_entry_cannot_take_an_end_frame(auth_client, admin):
    entry = {
        "endpoint_id": "fal-ai/flux-pro/kontext", "kind": "image_to_image", "label": "Kontext",
        "image_field": "image_url", "max_images": 1, "end_image_field": "tail_image_url",
    }
    message = _refused(auth_client.post("/api/generate/models", json=entry), 422, "invalid_model_entry")
    assert "end frame" in message


# ─── updating ────────────────────────────────────────────────────────────────


def test_patch_changes_only_the_fields_sent(auth_client, admin, models):
    before = auth_client.get("/api/generate/models").json()["data"]
    flux_before = next(m for m in before if m["endpoint_id"] == T2I)

    response = auth_client.patch(f"/api/generate/models/{models[T2I].id}", json={"note": "Good default"})

    assert response.status_code == 200, response.text
    after = response.json()["data"]
    assert after["note"] == "Good default"
    for field in ("label", "options", "unit_price", "price_unit", "sort_order", "is_active"):
        assert after[field] == flux_before[field]
    assert after["updated_at"] >= flux_before["updated_at"]


def test_patch_null_clears_a_nullable_field(auth_client, session, admin, models):
    response = auth_client.patch(
        f"/api/generate/models/{models[T2I].id}",
        json={"unit_price": None, "price_unit": None, "note": None},
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["unit_price"] is None
    assert body["price_unit"] is None
    assert body["note"] == ""
    session.expire_all()
    assert session.get(type(models[T2I]), models[T2I].id).unit_price is None


def test_patch_null_on_an_end_frame_field_removes_it(auth_client, admin, models):
    response = auth_client.patch(f"/api/generate/models/{models[I2V].id}", json={"end_image_field": None})
    assert response.status_code == 200
    assert response.json()["data"]["end_image_field"] is None


def test_patch_null_cannot_clear_a_required_field(auth_client, admin, models):
    _refused(
        auth_client.patch(f"/api/generate/models/{models[T2I].id}", json={"label": None}),
        422, "invalid_model_entry",
    )
    _refused(
        auth_client.patch(f"/api/generate/models/{models[I2I].id}", json={"image_field": None}),
        422, "invalid_model_entry",
    )


def test_patch_validates_the_row_it_would_leave(auth_client, admin, models):
    # Neither field alone is wrong; together they describe an edit endpoint as text →
    # image, which is the gecko-notes seed bug this rule exists for.
    message = _refused(
        auth_client.patch(f"/api/generate/models/{models[I2I].id}", json={"kind": "text_to_image"}),
        422, "invalid_model_entry",
    )
    assert "text-to-image" in message


def test_patch_to_an_existing_endpoint_is_a_conflict(auth_client, admin, models):
    _refused(
        auth_client.patch(f"/api/generate/models/{models[T2I].id}", json={"endpoint_id": I2I}),
        409, "endpoint_exists",
    )


def test_patch_can_switch_a_model_off(auth_client, admin, models):
    response = auth_client.patch(f"/api/generate/models/{models[T2I].id}", json={"is_active": False})
    assert response.json()["data"]["is_active"] is False
    assert T2I not in [m["endpoint_id"] for m in auth_client.get("/api/generate/models").json()["data"]]


def test_patch_an_unknown_model(auth_client, admin):
    _refused(auth_client.patch("/api/generate/models/nope", json={"note": "x"}), 404, "model_not_found")


# ─── deleting ────────────────────────────────────────────────────────────────


def test_an_admin_deletes_a_model(auth_client, admin, models):
    response = auth_client.delete(f"/api/generate/models/{models[T2I].id}")
    assert response.status_code == 204
    assert response.content == b""
    _refused(auth_client.delete(f"/api/generate/models/{models[T2I].id}"), 404, "model_not_found")


# ─── the fal key ─────────────────────────────────────────────────────────────


def test_the_fal_key_is_stored_and_never_returned(auth_client, session):
    assert auth_client.get("/api/settings/generation").json() == {"data": {"fal_key_configured": False}}

    response = auth_client.put("/api/settings/generation", json={"fal_api_key": "  fal-secret-123  "})

    assert response.status_code == 200
    assert response.json() == {"data": {"fal_key_configured": True}}
    assert "fal-secret-123" not in auth_client.get("/api/settings/generation").text
    # Encrypted at rest, and decrypts to what was typed, trimmed.
    row = session.exec(
        select(UserSetting).where(UserSetting.user_id == TEST_USER, UserSetting.key == FAL_API_KEY)
    ).one()
    assert "fal-secret-123" not in row.value
    assert json.loads(row.value).startswith("enc:")
    assert get_setting(session, TEST_USER, FAL_API_KEY) == "fal-secret-123"


def test_an_omitted_key_is_left_alone_and_an_empty_one_removes_it(auth_client):
    auth_client.put("/api/settings/generation", json={"fal_api_key": "fal-secret-123"})

    assert auth_client.put("/api/settings/generation", json={}).json()["data"]["fal_key_configured"] is True
    removed = auth_client.put("/api/settings/generation", json={"fal_api_key": ""})
    assert removed.json()["data"]["fal_key_configured"] is False


def test_generation_settings_require_authentication(client):
    assert client.get("/api/settings/generation").status_code == 401
