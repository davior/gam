"""What one generation was asked to do, and the checks it passed to be asked.

The request body is assembled at submit time, against the catalogue row as it was then,
and travels in the job's payload. The job never re-reads the row for its dialect: an
admin editing a model while a video is queued should not change what that video is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.generation.catalogue import CHOICES, extra_params_of
from app.generation.errors import GenerationError
from app.models.generation import (
    KIND_IMAGE_TO_IMAGE,
    KIND_IMAGE_TO_VIDEO,
    KIND_TEXT_TO_IMAGE,
    GenerationModel,
)

MAX_PROMPT_CHARS = 4000
NAME_LIMIT = 80

ROLE_BASE = "base"
ROLE_END_FRAME = "end_frame"


class InvalidGeneration(ValueError):
    """A request the catalogue row says the endpoint would refuse. The message says why."""


@dataclass(frozen=True)
class GenerationRequest:
    model_id: str
    endpoint_id: str
    kind: str
    prompt: str
    # The request body minus the prompt and the image fields: catalogue defaults, then
    # the user's choices. Exactly what `Asset.ai_parameters` will hold.
    parameters: dict
    image_field: Optional[str]
    image_field_is_list: bool
    end_image_field: Optional[str]
    # [{"asset_id", "role", "name"}], bases in order and then the end frame. `name` is
    # read at submit so the finished asset can say what it was made from even if the
    # base is deleted while the job waits.
    sources: list = field(default_factory=list)
    # The generated asset a regeneration started from, for the record.
    regenerated_from: Optional[str] = None

    @property
    def base_asset_ids(self) -> list[str]:
        return [s["asset_id"] for s in self.sources if s.get("role") == ROLE_BASE]

    @property
    def end_frame_asset_id(self) -> Optional[str]:
        ends = [s["asset_id"] for s in self.sources if s.get("role") == ROLE_END_FRAME]
        return ends[0] if ends else None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "GenerationRequest":
        try:
            request = cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})
        except (TypeError, KeyError) as exc:
            raise GenerationError("This generation job has no request to send") from exc
        if not request.endpoint_id or not isinstance(request.parameters, dict):
            raise GenerationError("This generation job has no request to send")
        return request


def check_prompt(prompt: Optional[str]) -> str:
    text = (prompt or "").strip()
    if not text:
        raise InvalidGeneration("Write a prompt first")
    if len(text) > MAX_PROMPT_CHARS:
        raise InvalidGeneration(
            f"The prompt is {len(text):,} characters; the limit is {MAX_PROMPT_CHARS:,}"
        )
    return text


def check_shape(row: GenerationModel, base_count: int, has_end_frame: bool) -> None:
    """The right number of bases for the kind, and an end frame only where one fits."""
    if row.kind == KIND_TEXT_TO_IMAGE and base_count:
        raise InvalidGeneration(
            f"{row.label} makes an image from a prompt alone and takes no base image"
        )
    if row.kind == KIND_IMAGE_TO_IMAGE and not 1 <= base_count <= row.max_images:
        raise InvalidGeneration(
            f"{row.label} takes one base image"
            if row.max_images == 1
            else f"{row.label} takes between 1 and {row.max_images} base images"
        )
    if row.kind == KIND_IMAGE_TO_VIDEO and base_count != 1:
        raise InvalidGeneration(f"{row.label} animates exactly one start image")
    if has_end_frame and not row.end_image_field:
        raise InvalidGeneration(f"{row.label} does not take an end frame")


def build_parameters(row: GenerationModel, options: dict, params: dict) -> dict:
    """Catalogue defaults, then the user's choices, each checked against the row.

    A choice is sent as the row declares it, not as it arrived: `"5"` and `5` are
    different requests to fal, and the declared value is the one known to work.
    """
    body = extra_params_of(row)

    for param, key in CHOICES:
        value = params.get(param)
        if value is None:
            continue
        allowed = options[key]
        name = param.replace("_", " ")
        if not allowed:
            article = "an" if name[0] in "aeiou" else "a"
            raise InvalidGeneration(f"{row.label} does not take {article} {name}")
        match = next((option for option in allowed if _same(option, value)), _NO_MATCH)
        if match is _NO_MATCH:
            raise InvalidGeneration(
                f"{name.capitalize()} must be one of: " + ", ".join(str(option) for option in allowed)
            )
        body[param] = match

    if params.get("seed") is not None:
        if not options["supports_seed"]:
            raise InvalidGeneration(f"{row.label} does not take a seed")
        body["seed"] = params["seed"]

    # An empty negative prompt is the form's way of saying "none"; it is not a request
    # for a feature the model lacks.
    negative = params.get("negative_prompt")
    if negative is not None and negative.strip():
        if not options["supports_negative_prompt"]:
            raise InvalidGeneration(f"{row.label} does not take a negative prompt")
        body["negative_prompt"] = negative.strip()

    if params.get("generate_audio") is not None:
        if not options["supports_audio"]:
            raise InvalidGeneration(f"{row.label} does not generate audio")
        body["generate_audio"] = params["generate_audio"]

    num_outputs = params.get("num_outputs", 1)
    max_outputs = options["max_outputs"]
    if not 1 <= num_outputs <= max_outputs:
        raise InvalidGeneration(
            f"{row.label} makes one at a time"
            if max_outputs == 1
            else f"Ask {row.label} for between 1 and {max_outputs} images"
        )
    # Sent only where the row declares more than one is possible: a row whose model has
    # no `num_images` field has max_outputs 1, and the field is not sent to it at all.
    if row.kind != KIND_IMAGE_TO_VIDEO and max_outputs > 1:
        body["num_images"] = num_outputs

    return without_app_fields(body, row.image_field, row.end_image_field)


def without_app_fields(body: dict, *image_fields: Optional[str]) -> dict:
    """The parameters with the fields the app owns taken out.

    The prompt and the images are added last when the request is sent, so a catalogue
    default could not override them anyway; removing them here keeps them out of
    `ai_parameters`, where a regeneration would otherwise resend a stale copy.
    """
    owned = {"prompt", *(f for f in image_fields if f)}
    return {key: value for key, value in body.items() if key not in owned}


def name_from_prompt(prompt: str, limit: int = NAME_LIMIT) -> str:
    """The prompt cut at a word boundary, for an asset or activity name."""
    text = " ".join(prompt.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    # Only back off to a space that leaves most of the budget; one enormous word is
    # cut mid-word rather than reduced to almost nothing.
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


_NO_MATCH = object()


def _same(declared: Any, given: Any) -> bool:
    """Equal and of the same kind: "5" is not 5, and True is not 1."""
    if isinstance(declared, bool) or isinstance(given, bool):
        return declared is given
    if isinstance(declared, str) or isinstance(given, str):
        return isinstance(declared, str) and isinstance(given, str) and declared == given
    return declared == given
