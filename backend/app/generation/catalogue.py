"""Reading and validating catalogue rows.

Strict on write, lenient on read. An admin saving a row is told precisely what is wrong
with it, because a row that saves and then 422s every generation is the failure this
catalogue exists to prevent. A row already stored is read with defaults filled in and
bad values dropped, so one hand-edited row cannot make the model list unreadable.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Optional

from app.models.generation import (
    GENERATION_KINDS,
    KIND_IMAGE_TO_VIDEO,
    KIND_TEXT_TO_IMAGE,
    PRICE_UNITS,
    GenerationModel,
)

# The user-facing choices, as `params` names them and as `options` lists their values.
CHOICES = (
    ("aspect_ratio", "aspect_ratios"),
    ("image_size", "image_sizes"),
    ("duration", "durations"),
    ("resolution", "resolutions"),
)
OPTION_LISTS = tuple(key for _param, key in CHOICES)
OPTION_FLAGS = ("supports_seed", "supports_negative_prompt", "supports_audio")
MAX_OUTPUTS = 4

DEFAULT_OPTIONS: dict[str, Any] = {
    **{key: [] for key in OPTION_LISTS},
    **{flag: False for flag in OPTION_FLAGS},
    "max_outputs": 1,
}

# An endpoint id becomes a URL path, so it is held to what fal's ids look like: at
# least two segments of letters, digits, dots, dashes and underscores. That rules out
# `..`, a query string, and anything else that would put the request somewhere other
# than the endpoint named.
_ENDPOINT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)+")
# A request body key.
_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class InvalidEntry(ValueError):
    """A catalogue row that cannot be saved. The message says which field and why."""


# ─── reading ─────────────────────────────────────────────────────────────────


def options_of(row: GenerationModel) -> dict[str, Any]:
    """A stored row's options, with every key present."""
    options = copy.deepcopy(DEFAULT_OPTIONS)
    stored = _json_object(row.options)
    for key in OPTION_LISTS:
        if isinstance(stored.get(key), list):
            options[key] = [v for v in stored[key] if _is_scalar(v)]
    for flag in OPTION_FLAGS:
        if isinstance(stored.get(flag), bool):
            options[flag] = stored[flag]
    if _is_int(stored.get("max_outputs")):
        options["max_outputs"] = stored["max_outputs"]
    return options


def extra_params_of(row: GenerationModel) -> dict[str, Any]:
    return _json_object(row.extra_params)


def _json_object(text: Optional[str]) -> dict:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# ─── validating ──────────────────────────────────────────────────────────────


def normalise_options(value: Any) -> dict[str, Any]:
    """An admin's `options`, checked, with defaults for whatever was left out.

    An unknown key is refused rather than kept: `"duration"` for `"durations"` would
    otherwise save, do nothing, and leave the form offering no durations at all.
    """
    if not isinstance(value, dict):
        raise InvalidEntry("options must be an object")

    options = copy.deepcopy(DEFAULT_OPTIONS)
    for key, item in value.items():
        if key in OPTION_LISTS:
            if not isinstance(item, list) or not all(_is_scalar(v) for v in item):
                raise InvalidEntry(f"options.{key} must be a list of strings or numbers")
            options[key] = item
        elif key in OPTION_FLAGS:
            if not isinstance(item, bool):
                raise InvalidEntry(f"options.{key} must be true or false")
            options[key] = item
        elif key == "max_outputs":
            if not _is_int(item):
                raise InvalidEntry("options.max_outputs must be a whole number")
            options[key] = item
        else:
            raise InvalidEntry(f"options has no setting called {key!r}")
    return options


def validate(entry: dict[str, Any]) -> dict[str, Any]:
    """Check a whole row — after a PATCH is merged, not just the fields it sent.

    Returns the entry with `options` normalised. The rules are the spec's, plus one it
    implies: a model that takes more than one base must take them as a list, since a
    single-image field cannot hold two.
    """
    endpoint_id = entry.get("endpoint_id")
    if not isinstance(endpoint_id, str) or not _ENDPOINT_ID.fullmatch(endpoint_id):
        raise InvalidEntry(
            "endpoint_id must be a fal endpoint id such as fal-ai/flux/dev"
        )

    kind = entry.get("kind")
    if kind not in GENERATION_KINDS:
        raise InvalidEntry(f"kind must be one of {', '.join(GENERATION_KINDS)}")

    label = entry.get("label")
    if not isinstance(label, str) or not label.strip():
        raise InvalidEntry("label is required")

    image_field = entry.get("image_field")
    end_image_field = entry.get("end_image_field")
    max_images = entry.get("max_images")
    if not _is_int(max_images):
        raise InvalidEntry("max_images must be a whole number")

    for name, value in (("image_field", image_field), ("end_image_field", end_image_field)):
        if value is not None and (not isinstance(value, str) or not _FIELD_NAME.fullmatch(value)):
            raise InvalidEntry(f"{name} must be a request field name, such as image_url")

    if kind == KIND_TEXT_TO_IMAGE:
        if max_images != 0:
            raise InvalidEntry(
                "A text-to-image model takes no base images, so max_images must be 0"
            )
        if image_field is not None:
            raise InvalidEntry(
                "A text-to-image model takes no base images, so it has no image_field"
            )
    else:
        if max_images < 1:
            raise InvalidEntry(
                "This kind of model needs at least one base image: max_images must be 1 or more"
            )
        if image_field is None:
            raise InvalidEntry(
                "This kind of model needs an image_field to send its base image in"
            )
        if max_images > 1 and not entry.get("image_field_is_list"):
            raise InvalidEntry(
                "A model that takes more than one base image needs image_field_is_list"
            )

    if end_image_field is not None and kind != KIND_IMAGE_TO_VIDEO:
        raise InvalidEntry("Only an image-to-video model takes an end frame")

    options = normalise_options(entry.get("options", {}))
    max_outputs = options["max_outputs"]
    if kind == KIND_IMAGE_TO_VIDEO:
        if max_outputs != 1:
            raise InvalidEntry(
                "A video model makes one video at a time: options.max_outputs must be 1"
            )
    elif not 1 <= max_outputs <= MAX_OUTPUTS:
        raise InvalidEntry(f"options.max_outputs must be between 1 and {MAX_OUTPUTS}")

    if not isinstance(entry.get("extra_params", {}), dict):
        raise InvalidEntry("extra_params must be an object")

    unit_price = entry.get("unit_price")
    if unit_price is not None and (isinstance(unit_price, bool) or unit_price < 0):
        raise InvalidEntry("unit_price cannot be negative")
    price_unit = entry.get("price_unit")
    if price_unit is not None and price_unit not in PRICE_UNITS:
        raise InvalidEntry(f"price_unit must be one of {', '.join(PRICE_UNITS)}")

    return {**entry, "options": options}


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
