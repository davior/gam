"""Each user's default model per kind: saved in settings, applied to the catalogue list.

The list puts the default first in its kind, not only flags it, because Video Creator
(and anything else that takes the first model of a kind) is meant to follow the choice
without knowing the setting exists.
"""

import pytest

from app.models.generation import GenerationModel
from tests.fal_stub import I2I, I2V, T2I, VEO, seed_models


@pytest.fixture
def models(session):
    return seed_models(session)


def _order(client, kind):
    rows = client.get("/api/generate/models").json()["data"]
    return [(m["endpoint_id"], m["is_default"]) for m in rows if m["kind"] == kind]


def _set(client, **defaults):
    return client.put("/api/settings/generation", json={"default_models": defaults})


def test_no_default_keeps_the_catalogue_order(auth_client, models):
    assert _order(auth_client, "image_to_video") == [(I2V, False), (VEO, False)]
    assert auth_client.get("/api/settings/generation").json()["data"]["default_models"] == {}


def test_a_default_is_saved_flagged_and_listed_first(auth_client, models):
    response = _set(auth_client, image_to_video=VEO)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["default_models"] == {"image_to_video": VEO}
    assert _order(auth_client, "image_to_video") == [(VEO, True), (I2V, False)]
    # Other kinds are untouched.
    assert _order(auth_client, "text_to_image") == [(T2I, False)]


def test_setting_one_kind_keeps_the_others_and_null_clears(auth_client, models):
    _set(auth_client, image_to_video=VEO, text_to_image=T2I)
    _set(auth_client, image_to_image=I2I)
    body = auth_client.get("/api/settings/generation").json()["data"]
    assert body["default_models"] == {
        "image_to_video": VEO, "text_to_image": T2I, "image_to_image": I2I,
    }

    _set(auth_client, image_to_video=None, text_to_image="")
    body = auth_client.get("/api/settings/generation").json()["data"]
    assert body["default_models"] == {"image_to_image": I2I}
    assert _order(auth_client, "image_to_video") == [(I2V, False), (VEO, False)]


def test_saving_the_key_alone_leaves_the_defaults(auth_client, models):
    _set(auth_client, image_to_video=VEO)
    auth_client.put("/api/settings/generation", json={"fal_api_key": "fal-key"})
    assert auth_client.get("/api/settings/generation").json()["data"]["default_models"] == {
        "image_to_video": VEO
    }


@pytest.mark.parametrize(
    "defaults",
    [
        {"image_to_video": T2I},              # a model of another kind
        {"text_to_image": "fal-ai/nowhere/x"},  # not in the catalogue
        {"text_to_video": T2I},               # no such kind
        {"text_to_image": 7},                 # not an endpoint id
    ],
)
def test_a_default_that_cannot_be_used_is_refused_and_nothing_saved(auth_client, models, defaults):
    response = _set(auth_client, image_to_image=I2I, **defaults)

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_default_model"
    assert auth_client.get("/api/settings/generation").json()["data"]["default_models"] == {}


def test_a_switched_off_default_falls_back_to_the_catalogue_order(auth_client, session, models):
    _set(auth_client, image_to_video=VEO)
    veo = session.get(GenerationModel, models[VEO].id)
    veo.is_active = False
    session.add(veo)
    session.commit()

    assert _order(auth_client, "image_to_video") == [(I2V, False)]
    assert auth_client.get("/api/settings/generation").json()["data"]["default_models"] == {}
    # A switched-off model cannot be chosen either.
    assert _set(auth_client, image_to_video=VEO).status_code == 422
