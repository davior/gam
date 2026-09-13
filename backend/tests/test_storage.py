"""Local storage, and the key validation that keeps user input off the filesystem."""

import hashlib
from pathlib import Path

import pytest

from app.storage import LocalStorage, StorageError, new_key, thumb_key_for, validate_key


@pytest.fixture(name="storage")
def storage_fixture(tmp_path):
    return LocalStorage(tmp_path)


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


# ─── key validation: the security boundary ───────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "user1/../../../etc/passwd",
        "user1/..%2f..%2fetc",
        "../user1/file.jpg",
        "/etc/passwd",
        "user1//file.jpg",
        "user1/./file.jpg",
        "user1/.hidden",
        ".hidden/file.jpg",
        "user1/sub/dir/file.jpg",  # three segments — the layout is exactly two
        "user1",                    # one segment
        "user1/file\x00.jpg",
        "user1/file\n.jpg",
        "user1/file with spaces.jpg",
        "",
    ],
)
def test_unsafe_keys_are_refused(key):
    with pytest.raises(StorageError):
        validate_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "user1/abc123.jpg",
        "user-1/1e4f-9a.mp4",
        "u/a.thumb.jpg",
        "user1/no-extension",
    ],
)
def test_safe_keys_are_accepted(key):
    assert validate_key(key) == key


def test_unsafe_keys_are_refused_at_every_entry_point(storage):
    """Validation is not only at construction — every method re-checks.

    A key can reach `open` or `delete` from a database row, not just from `new_key`,
    and a row is only as trustworthy as whatever wrote it.
    """
    bad = "user1/../escape.jpg"
    with pytest.raises(StorageError):
        storage.open(bad)
    with pytest.raises(StorageError):
        storage.stat(bad)
    # delete is the exception: it never raises, because cleanup paths call it with
    # whatever they have. It must still refuse to act.
    assert storage.delete(bad) is False


def test_symlink_escaping_the_root_is_refused(storage, tmp_path):
    """validate_key cannot catch this — a symlink is only visible after resolving."""
    outside = tmp_path.parent / "outside-the-root"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("private")

    owner_dir = Path(storage.root) / "user1"
    owner_dir.mkdir(parents=True, exist_ok=True)
    (owner_dir / "link.txt").symlink_to(outside / "secret.txt")

    # The symlink resolves outside the storage root, so it must not be readable.
    with pytest.raises(StorageError):
        storage.open("user1/link.txt")


# ─── key generation ──────────────────────────────────────────────────────────


def test_new_key_is_unique_and_owned():
    first = new_key("user1", ".jpg")
    second = new_key("user1", ".jpg")
    assert first != second
    assert first.startswith("user1/")
    assert first.endswith(".jpg")


def test_new_key_normalises_the_extension():
    assert new_key("u", "JPG").endswith(".jpg")
    assert new_key("u", ".PNG").endswith(".png")
    assert "." not in new_key("u", "").split("/")[1]


@pytest.mark.parametrize("extension", ["../x", ".jp g", ".j/g", ".verylongextension123"])
def test_new_key_refuses_an_unsafe_extension(extension):
    """The extension is the only part of a key derived from the uploaded filename."""
    with pytest.raises(StorageError):
        new_key("user1", extension)


def test_thumb_key_is_derived_not_stored():
    assert thumb_key_for("user1/abc.mp4") == "user1/abc.thumb.jpg"
    assert thumb_key_for("user1/abc.jpeg") == "user1/abc.thumb.jpg"


# ─── writing and reading ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_write_stream_round_trip(storage):
    key = new_key("user1", ".bin")
    stored = await storage.write_stream(key, _chunks(b"hello ", b"world"))

    assert stored.size_bytes == 11
    assert stored.sha256 == hashlib.sha256(b"hello world").hexdigest()
    with storage.open(key) as handle:
        assert handle.read() == b"hello world"


@pytest.mark.anyio
async def test_write_stream_leaves_nothing_behind_on_failure(storage):
    """An interrupted upload must not leave something that looks complete."""

    async def exploding():
        yield b"partial data"
        raise RuntimeError("connection dropped")

    key = new_key("user1", ".bin")
    with pytest.raises(RuntimeError):
        await storage.write_stream(key, exploding())

    assert storage.stat(key).exists is False
    # And no .partial debris either.
    assert list(Path(storage.root).rglob("*.partial")) == []


@pytest.mark.anyio
async def test_empty_chunks_are_skipped(storage):
    key = new_key("user1", ".bin")
    stored = await storage.write_stream(key, _chunks(b"a", b"", b"b"))
    assert stored.size_bytes == 2


def test_write_bytes_round_trip(storage):
    key = new_key("user1", ".jpg")
    stored = storage.write_bytes(key, b"\xff\xd8\xff")
    assert stored.size_bytes == 3
    with storage.open(key) as handle:
        assert handle.read() == b"\xff\xd8\xff"


def test_open_missing_object_raises(storage):
    with pytest.raises(StorageError):
        storage.open("user1/does-not-exist.jpg")


# ─── materialise ─────────────────────────────────────────────────────────────


def test_materialise_gives_a_real_path(storage):
    """What ffmpeg and Pillow need. Free locally; a download for a remote backend."""
    key = new_key("user1", ".bin")
    storage.write_bytes(key, b"payload")

    with storage.materialise(key) as path:
        assert path.is_file()
        assert path.read_bytes() == b"payload"


def test_materialise_missing_object_raises(storage):
    with pytest.raises(StorageError):
        with storage.materialise("user1/missing.bin"):
            pass


# ─── stat and delete ─────────────────────────────────────────────────────────


def test_stat_reports_absence_rather_than_raising(storage):
    stat = storage.stat("user1/missing.bin")
    assert stat.exists is False
    assert stat.size_bytes == 0


def test_delete_is_idempotent(storage):
    key = new_key("user1", ".bin")
    storage.write_bytes(key, b"x")

    assert storage.delete(key) is True
    # "Already gone" is success for a cleanup path, not an error.
    assert storage.delete(key) is False


def test_usage_counts_only_that_owner(storage):
    storage.write_bytes(new_key("user1", ".bin"), b"x" * 100)
    storage.write_bytes(new_key("user1", ".bin"), b"x" * 50)
    storage.write_bytes(new_key("user2", ".bin"), b"x" * 999)

    assert storage.usage_bytes("user1") == 150
    assert storage.usage_bytes("user2") == 999
    assert storage.usage_bytes("nobody") == 0
