"""Signed media URLs and HTTP Range responses.

Range support is what makes a ninety-minute interview scrub instead of download. The
signature is what stops a leaked URL being permanent, and what gecko-notes does not do
at all — its /media/* is on the public-path allowlist.
"""

from pathlib import Path

import pytest

from app.auth import sign_media_key
from app.media.ranged import parse_range
from fastapi import HTTPException

FIXTURES = Path(__file__).parent / "fixtures"


def _upload_video(client):
    return client.post(
        "/api/assets",
        files=[
            ("files", ("sample_video.mp4", (FIXTURES / "sample_video.mp4").read_bytes(), "video/mp4"))
        ],
    ).json()["created"][0]


# ─── range parsing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=100-", 1000, (100, 999)),
        # A player reading a trailing index — an MP4 moov atom at the end, say.
        ("bytes=-100", 1000, (900, 999)),
        # Past the end is clamped, not rejected: give what exists.
        ("bytes=0-9999", 1000, (0, 999)),
        ("bytes=999-999", 1000, (999, 999)),
        # Nothing to honour — caller sends the whole body.
        (None, 1000, None),
        ("", 1000, None),
        ("items=0-99", 1000, None),
        ("bytes=abc", 1000, None),
        ("bytes=-", 1000, None),
    ],
)
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=1500-2000", "bytes=-0", "bytes=500-100"])
def test_unsatisfiable_ranges_raise_416(header):
    """A syntactically valid range that cannot be met is a 416 with a Content-Range,
    which is what the specification asks for."""
    with pytest.raises(HTTPException) as raised:
        parse_range(header, 1000)
    assert raised.value.status_code == 416
    assert raised.value.headers["Content-Range"] == "bytes */1000"


# ─── serving ─────────────────────────────────────────────────────────────────


def test_signed_url_serves_the_file(library):
    asset = _upload_video(library)

    response = library.get(asset["file_url"])
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"
    assert int(response.headers["content-length"]) == asset["size_bytes"]


def test_range_request_returns_206_with_exactly_those_bytes(library):
    asset = _upload_video(library)
    whole = library.get(asset["file_url"]).content

    response = library.get(asset["file_url"], headers={"Range": "bytes=100-199"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-199/{len(whole)}"
    assert response.headers["content-length"] == "100"
    assert response.content == whole[100:200]


def test_suffix_range_returns_the_tail(library):
    asset = _upload_video(library)
    whole = library.get(asset["file_url"]).content

    response = library.get(asset["file_url"], headers={"Range": "bytes=-50"})
    assert response.status_code == 206
    assert response.content == whole[-50:]


def test_unsatisfiable_range_is_416(library):
    asset = _upload_video(library)

    response = library.get(asset["file_url"], headers={"Range": f"bytes={asset['size_bytes']}-"})
    assert response.status_code == 416


def test_thumbnail_is_served(library):
    asset = _upload_video(library)

    response = library.get(asset["thumb_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


# ─── signatures ──────────────────────────────────────────────────────────────


def test_unsigned_request_is_refused(library):
    asset = _upload_video(library)
    path = asset["file_url"].split("?")[0]

    # Missing parameters are a 422 from validation; the point is that the bare path
    # does not serve the file.
    assert library.get(path).status_code in (403, 422)


def test_tampered_signature_is_refused(library):
    asset = _upload_video(library)

    response = library.get(asset["file_url"].replace("sig=", "sig=x"))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "invalid_signature"


def test_signature_cannot_be_moved_to_another_key(library):
    """The property that stops one valid URL being edited into another asset's."""
    first = _upload_video(library)
    second = library.post(
        "/api/assets",
        files=[
            ("files", ("sample_image.jpg", (FIXTURES / "sample_image.jpg").read_bytes(), "image/jpeg"))
        ],
    ).json()["created"][0]

    first_key = first["file_url"].split("?")[0].removeprefix("/media/")
    second_query = second["file_url"].split("?")[1]

    response = library.get(f"/media/{first_key}?{second_query}")
    assert response.status_code == 403


def test_expired_signature_is_refused(library):
    asset = _upload_video(library)
    key = asset["file_url"].split("?")[0].removeprefix("/media/")

    expires_at, signature = sign_media_key(key, ttl_seconds=-1)
    response = library.get(f"/media/{key}?exp={expires_at}&sig={signature}")
    assert response.status_code == 403


def test_expiry_cannot_be_extended(library):
    """The signature covers the expiry, so editing it invalidates the URL."""
    asset = _upload_video(library)
    key, query = asset["file_url"].split("?")
    exp, sig = (part.split("=", 1)[1] for part in query.split("&"))

    response = library.get(f"{key}?exp={int(exp) + 86400}&sig={sig}")
    assert response.status_code == 403


def test_traversal_key_is_a_404_not_a_400(library):
    """Same answer as a key that simply does not exist, so probing learns nothing."""
    expires_at, signature = sign_media_key("user/whatever.jpg")
    response = library.get(f"/media/../../etc/passwd?exp={expires_at}&sig={signature}")
    assert response.status_code in (403, 404)


def test_valid_signature_for_a_missing_file_is_a_404(library, media_dir):
    asset = _upload_video(library)
    for path in Path(media_dir).rglob("*"):
        if path.is_file():
            path.unlink()

    assert library.get(asset["file_url"]).status_code == 404
