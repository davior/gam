"""Disk space: the Settings breakdown, the header's level, the stray sweep, and the
guard that refuses new writes when the disk is nearly full.

The disk is the `disk` fixture's (conftest.py). `statvfs` cannot be made to report a
full disk on demand, and a suite whose result depended on the runner's free space would
not be testing anything.
"""

import os
import socket
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from sqlmodel import func, select

from app.config import settings
from app.models.asset import Asset
from app.models.job import EnrichmentJob
from app.storage import capacity, thumb_key_for
from app.storage.capacity import DISK_FULL_CODE, DISK_FULL_MESSAGE, GB
from tests.fal_stub import T2I, seed_models, set_fal_key

FIXTURES = Path(__file__).parent / "fixtures"
TEST_USER = "user-under-test"
DAY = 24 * 3600


def _upload(client, name: str, media_type: str) -> dict:
    response = client.post(
        "/api/assets", files=[("files", (name, (FIXTURES / name).read_bytes(), media_type))]
    )
    assert response.status_code == 201, response.text
    return response.json()["created"][0]


def _summary(client) -> dict:
    response = client.get("/api/storage")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _status(client) -> dict:
    response = client.get("/api/storage/status")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _write(path: Path, size: int, *, age: float = 2 * DAY) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    past = time.time() - age
    os.utime(path, (past, past))
    return path


def _row(session, *, user_id: str = TEST_USER, size: int = 0, kind: str = "video", **fields) -> Asset:
    fields.setdefault("storage_key", f"{user_id}/{uuid.uuid4()}.mp4")
    asset = Asset(user_id=user_id, name=f"{kind} {size}", asset_type=kind, size_bytes=size, **fields)
    session.add(asset)
    session.commit()
    return asset


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_users", "tester")


# ─── one user's library ──────────────────────────────────────────────────────


def test_the_library_is_broken_down_by_type_with_its_thumbnails(library, media_dir):
    _upload(library, "sample_image.jpg", "image/jpeg")
    _upload(library, "sample_image.png", "image/png")
    _upload(library, "sample_document.txt", "text/plain")

    library_usage = _summary(library)["library"]

    images = (FIXTURES / "sample_image.jpg").stat().st_size + (FIXTURES / "sample_image.png").stat().st_size
    text = (FIXTURES / "sample_document.txt").stat().st_size
    assert library_usage["by_type"] == [
        {"asset_type": "image", "files": 2, "bytes": images},
        {"asset_type": "document", "files": 1, "bytes": text},
    ]
    assert library_usage["files"] == 3

    # Counted on disk: thumbnail sizes are recorded nowhere else.
    thumbs = list((media_dir / TEST_USER).glob("*.thumb.jpg"))
    assert thumbs, "the images should have produced previews"
    assert library_usage["thumbnails"] == {
        "files": len(thumbs),
        "bytes": sum(path.stat().st_size for path in thumbs),
    }
    assert library_usage["total_bytes"] == images + text + library_usage["thumbnails"]["bytes"]


def test_window_clips_and_other_peoples_files_are_not_counted(library, session):
    mine = _row(session, size=1000)
    # A window clip owns no bytes, whatever its size column says.
    _row(session, size=10**12, storage_key=None, parent_asset_id=mine.id)
    _row(session, user_id="someone-else", size=10**12)

    library_usage = _summary(library)["library"]

    assert library_usage["by_type"] == [{"asset_type": "video", "files": 1, "bytes": 1000}]
    assert [row["id"] for row in library_usage["largest"]] == [mine.id]


def test_the_largest_are_my_ten_biggest_files_biggest_first(library, session):
    rows = [_row(session, size=size * 1_000_000) for size in range(1, 13)]
    _row(session, user_id="someone-else", size=10**12)

    largest = _summary(library)["library"]["largest"]

    assert [row["id"] for row in largest] == [row.id for row in reversed(rows)][:10]
    assert largest[0] == {
        "id": rows[-1].id,
        "name": rows[-1].name,
        "asset_type": "video",
        "size_bytes": 12_000_000,
    }


def test_an_empty_library_is_all_zeros(library):
    library_usage = _summary(library)["library"]

    assert library_usage == {
        "total_bytes": 0,
        "files": 0,
        "by_type": [],
        "thumbnails": {"files": 0, "bytes": 0},
        "largest": [],
    }


# ─── the server ──────────────────────────────────────────────────────────────


def test_server_numbers_are_for_admins_only(library, monkeypatch):
    assert _summary(library)["server"] is None

    monkeypatch.setattr(settings, "admin_users", "tester")
    assert _summary(library)["server"] is not None


def test_the_server_reports_the_disk_and_every_users_files(library, admin, disk, media_dir, session):
    _upload(library, "sample_image.jpg", "image/jpeg")
    _write(media_dir / "someone-else" / f"{uuid.uuid4()}.mp4", 5000, age=0)
    _row(session, user_id="someone-else", storage_key=f"someone-else/{uuid.uuid4()}.mp4")
    disk.set(free_gb=400, total_gb=1000)

    server = _summary(library)["server"]

    (volume,) = server["volumes"]
    assert volume["roles"] == ["media", "database", "working"]
    assert volume["total_bytes"] == 1000 * GB
    assert volume["free_bytes"] == 400 * GB
    assert volume["level"] == "ok"
    assert volume["guarded"] is True

    on_disk = [path for path in media_dir.rglob("*") if path.is_file()]
    assert server["media"] == {
        "files": len(on_disk),
        "bytes": sum(path.stat().st_size for path in on_disk),
    }
    assert server["warn_free_bytes"] == 20 * GB
    assert server["reserve_bytes"] == 5 * GB


def test_free_space_is_what_can_be_written_not_total_minus_used(library, admin, disk):
    # ext4's root reserve: used + free falls short of total, and the gap is not free.
    disk.set(free_gb=100, total_gb=1000, reserved_gb=50)

    (volume,) = _summary(library)["server"]["volumes"]

    assert volume["free_bytes"] == 100 * GB
    assert volume["used_bytes"] == 850 * GB


def test_the_database_counts_its_write_ahead_log(library, admin, monkeypatch, tmp_path):
    database = tmp_path / "db" / "gam.db"
    _write(database, 1000, age=0)
    _write(Path(f"{database}-wal"), 400, age=0)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database}")

    assert _summary(library)["server"]["database_bytes"] == 1400


def test_a_database_that_is_not_a_file_reports_no_size(library, admin, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://db/gam")

    server = _summary(library)["server"]

    assert server["database_bytes"] is None
    assert server["volumes"][0]["roles"] == ["media", "working"]


def test_separate_disks_are_listed_separately(library, admin, disk):
    disk.devices = {tempfile.gettempdir(): 2}

    media, working = _summary(library)["server"]["volumes"]

    assert media["roles"] == ["media", "database"]
    assert working["roles"] == ["working"]
    assert working["guarded"] is False


def test_a_small_temp_disk_alone_does_not_raise_the_level(library, admin, disk, monkeypatch):
    # A laptop's /tmp is often a tmpfs sized from its memory. Nothing persistent is on
    # it, so it is shown but does not warn, and does not block anything.
    temp = tempfile.gettempdir()
    disk.devices = {temp: 2}
    roomy = disk.usage(None)
    cramped = type(roomy)(total=4 * GB, used=3 * GB, free=1 * GB)
    monkeypatch.setattr(capacity, "_disk_usage", lambda path: cramped if str(path) == temp else roomy)

    summary = _summary(library)

    assert summary["level"] == "ok"
    assert summary["server"]["volumes"][1]["level"] == "critical"
    assert _status(library)["level"] == "ok"
    _upload(library, "sample_document.txt", "text/plain")


# ─── the header's level ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("free_gb", "level"),
    [(100, "ok"), (20, "ok"), (19.9, "low"), (5, "low"), (4.9, "critical"), (0, "critical")],
)
def test_the_level_follows_the_thresholds(library, admin, disk, free_gb, level):
    disk.set(free_gb=free_gb, total_gb=200)

    status = _status(library)

    assert status["level"] == level
    assert status["free_bytes"] == int(free_gb * GB)
    assert status["total_bytes"] == 200 * GB


def test_the_thresholds_are_settings(library, disk, monkeypatch):
    disk.set(free_gb=50)
    monkeypatch.setattr(settings, "disk_warn_free_gb", 100)
    assert _status(library)["level"] == "low"

    monkeypatch.setattr(settings, "disk_reserve_gb", 60)
    assert _status(library)["level"] == "critical"


def test_everyone_gets_the_level_and_only_admins_get_the_numbers(library, disk):
    disk.set(free_gb=10)

    assert _status(library) == {"level": "low", "free_bytes": None, "total_bytes": None}
    summary = _summary(library)
    assert summary["level"] == "low"
    assert summary["server"] is None


def test_an_unreadable_disk_reads_as_ok_rather_than_failing(library, admin, monkeypatch):
    def unreadable(path):
        raise OSError("statvfs failed")

    monkeypatch.setattr(capacity, "_disk_usage", unreadable)

    assert _status(library) == {"level": "ok", "free_bytes": None, "total_bytes": None}
    summary = _summary(library)
    assert summary["server"]["volumes"] == []
    assert summary["level"] == "ok"


def test_storage_needs_a_session(client):
    assert client.get("/api/storage").status_code == 401
    assert client.get("/api/storage/status").status_code == 401
    assert client.post("/api/storage/cleanup").status_code == 401


# ─── stray files ─────────────────────────────────────────────────────────────


@pytest.fixture
def strewn(library, media_dir, session):
    """A library with every kind of file the sweep has to tell apart."""
    kept = _upload(library, "sample_image.jpg", "image/jpeg")
    own = media_dir / TEST_USER
    for path in own.iterdir():
        # Old enough to be swept, so only being referenced is keeping them.
        _write(path, path.stat().st_size)

    # A poster derivable from its asset's key, though the row never recorded it.
    unrecorded = _row(session, size=10)
    _write(media_dir / thumb_key_for(unrecorded.storage_key), 30)
    _write(media_dir / unrecorded.storage_key, 10)

    strays = [
        _write(own / f"{uuid.uuid4()}.mp4", 1000),  # a delete whose unlink failed
        _write(own / f"{uuid.uuid4()}.mp4.partial", 200),  # an interrupted upload
        _write(media_dir / "someone-else" / f"{uuid.uuid4()}.jpg", 30),  # anybody's
    ]
    kept_files = [
        *own.glob("*.jpg"),
        media_dir / thumb_key_for(unrecorded.storage_key),
        media_dir / unrecorded.storage_key,
        _write(own / f"{uuid.uuid4()}.mp4", 5000, age=60),  # its row may be a moment away
        _write(own / ".DS_Store", 6, age=2 * DAY),  # not a name GAM writes
        _write(own / "nested" / f"{uuid.uuid4()}.mp4", 7),  # not the layout
        _write(media_dir / "loose.txt", 8),  # not the layout
    ]
    return {"asset": kept, "strays": strays, "kept": [p for p in kept_files if p not in strays]}


def test_strays_are_old_files_nothing_refers_to(library, admin, strewn):
    stray = _summary(library)["server"]["stray"]

    assert stray == {"files": 3, "bytes": 1230}


def test_cleanup_removes_the_strays_and_nothing_else(library, admin, strewn):
    response = library.post("/api/storage/cleanup")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"removed": {"files": 3, "bytes": 1230}, "skipped": 0}
    assert not any(path.exists() for path in strewn["strays"])
    assert all(path.exists() for path in strewn["kept"])
    assert _summary(library)["server"]["stray"] == {"files": 0, "bytes": 0}
    # The asset that was there is still whole.
    asset = library.get(f"/api/assets/{strewn['asset']['id']}").json()["data"]
    assert asset["missing"] is False


def test_cleanup_is_for_admins_only(library, strewn):
    response = library.post("/api/storage/cleanup")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "admin_required"
    assert all(path.exists() for path in strewn["strays"])


def test_a_poster_shared_with_a_clip_is_not_a_stray(library, admin, session, media_dir):
    parent = _row(session, size=10)
    poster = _write(media_dir / thumb_key_for(parent.storage_key), 30)
    # The clip copies the parent's poster key; deleting the parent leaves it in use.
    _row(session, storage_key=None, thumb_key=thumb_key_for(parent.storage_key), parent_asset_id=None)
    session.delete(parent)
    session.commit()

    library.post("/api/storage/cleanup")

    assert poster.exists()


# ─── refusing writes when the disk is nearly full ────────────────────────────


def test_an_upload_below_the_reserve_is_refused_as_disk_full(library, disk, session):
    disk.set(free_gb=1)

    response = library.post(
        "/api/assets",
        files=[
            ("files", ("a.txt", b"one", "text/plain")),
            ("files", ("b.txt", b"two", "text/plain")),
        ],
    )

    assert response.status_code == 507
    assert response.json()["detail"] == {"code": DISK_FULL_CODE, "message": DISK_FULL_MESSAGE}
    assert session.exec(select(func.count()).select_from(Asset)).one() == 0


def test_files_that_fit_are_stored_and_the_rest_say_why_not(library, disk):
    disk.free = 5 * GB + 1000

    response = library.post(
        "/api/assets",
        files=[
            ("files", ("small.txt", b"x" * 10, "text/plain")),
            ("files", ("large.txt", b"x" * 5000, "text/plain")),
        ],
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert [asset["original_name"] for asset in body["created"]] == ["small.txt"]
    assert body["rejected"] == [
        {"filename": "large.txt", "code": DISK_FULL_CODE, "message": DISK_FULL_MESSAGE}
    ]


def test_an_unreadable_disk_does_not_block_uploads(library, monkeypatch):
    def unreadable(path):
        raise OSError("statvfs failed")

    monkeypatch.setattr(capacity, "_disk_usage", unreadable)

    _upload(library, "sample_document.txt", "text/plain")


def _refused(response):
    assert response.status_code == 507, response.text
    assert response.json()["detail"]["code"] == DISK_FULL_CODE


def _jobs(session) -> int:
    return session.exec(select(func.count()).select_from(EnrichmentJob)).one()


def test_an_import_is_refused_before_it_is_queued(library, disk, session, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    disk.set(free_gb=1)

    _refused(library.post("/api/assets/import", json={"url": "https://www.youtube.com/watch?v=abc"}))
    assert _jobs(session) == 0


def test_a_generation_is_refused_before_it_is_queued(library, disk, session):
    models = seed_models(session)
    set_fal_key(session)
    disk.set(free_gb=1)

    _refused(library.post("/api/generate", json={"model_id": models[T2I].id, "prompt": "A fox"}))
    assert _jobs(session) == 0


def test_a_bad_generation_request_is_still_told_what_is_wrong(library, disk, session):
    set_fal_key(session)
    disk.set(free_gb=1)

    response = library.post("/api/generate", json={"model_id": "nope", "prompt": "A fox"})

    assert response.status_code == 404


def test_extractions_and_promotions_are_refused_before_they_are_queued(library, disk, session):
    video = _upload(library, "sample_video.mp4", "video/mp4")
    clip = library.post(
        f"/api/assets/{video['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    ).json()["data"]
    disk.set(free_gb=1)

    _refused(library.post(f"/api/assets/{video['id']}/subvideo", json={"in_point": 0.0, "out_point": 0.5}))
    _refused(library.post(f"/api/assets/{clip['id']}/promote"))
    assert _jobs(session) == 0


def test_a_clip_still_works_on_a_full_disk(library, disk):
    # A window into its parent's bytes writes none of its own, so there is no reason
    # to refuse it.
    video = _upload(library, "sample_video.mp4", "video/mp4")
    disk.set(free_gb=1)

    response = library.post(
        f"/api/assets/{video['id']}/clips", json={"in_point": 0.0, "out_point": 0.5}
    )

    assert response.status_code == 201, response.text
