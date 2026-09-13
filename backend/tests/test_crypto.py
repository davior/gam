"""Key encryption and media URL signing."""

import time

import pytest

from app.auth import (
    decrypt_api_key,
    encrypt_api_key,
    sign_media_key,
    verify_media_signature,
)


def test_round_trip():
    secret = "sk-not-a-real-key"
    stored = encrypt_api_key(secret)
    assert stored != secret
    assert stored.startswith("enc:")
    assert decrypt_api_key(stored) == secret


def test_encryption_is_not_deterministic():
    """Two encryptions of the same key must not be byte-identical, or the database
    leaks which users share a provider key."""
    assert encrypt_api_key("same") != encrypt_api_key("same")


def test_plaintext_is_passed_through():
    """A value written before encryption existed keeps working rather than reading as
    corrupt, so an existing database can be upgraded in place."""
    assert decrypt_api_key("sk-legacy-plaintext") == "sk-legacy-plaintext"


def test_empty_values():
    assert encrypt_api_key("") == ""
    assert decrypt_api_key("") == ""


def test_corrupt_ciphertext_reads_as_unset():
    assert decrypt_api_key("enc:not-valid-ciphertext") == ""


# ─── media signatures ────────────────────────────────────────────────────────


def test_signature_round_trip():
    key = "user-1/abc123.mp4"
    expires_at, signature = sign_media_key(key)
    assert verify_media_signature(key, expires_at, signature)


def test_signature_is_bound_to_the_key():
    """The property that stops one valid URL being edited into another asset's."""
    expires_at, signature = sign_media_key("user-1/abc123.mp4")
    assert not verify_media_signature("user-1/secret.mp4", expires_at, signature)


def test_signature_is_bound_to_the_expiry():
    """So an expiry cannot simply be extended by editing the URL."""
    expires_at, signature = sign_media_key("user-1/abc123.mp4")
    assert not verify_media_signature("user-1/abc123.mp4", expires_at + 3600, signature)


def test_expired_signature_is_rejected():
    key = "user-1/abc123.mp4"
    expires_at, signature = sign_media_key(key, ttl_seconds=-1)
    assert not verify_media_signature(key, expires_at, signature)


def test_garbage_signature_is_rejected():
    expires_at, _ = sign_media_key("user-1/abc123.mp4")
    assert not verify_media_signature("user-1/abc123.mp4", expires_at, "nonsense")
