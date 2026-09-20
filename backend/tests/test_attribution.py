"""Composition and inheritance rules for attribution (M10).

`app/attribution.py` is pure on purpose — no session, no I/O — so every rule in it can
be driven directly here with plain objects, rather than through an upload and a clip.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.attribution import (
    ATTRIBUTION_FIELDS,
    ATTRIBUTION_TEXT_FIELDS,
    compose_credit,
    index_text,
    resolve,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def attributed(**fields):
    """An object shaped like an Asset for the fields this module reads."""
    values = {name: None for name in ATTRIBUTION_FIELDS}
    values.update(fields)
    return SimpleNamespace(**values)


# ─── composition ─────────────────────────────────────────────────────────────


def test_a_full_credit_reads_in_order():
    asset = attributed(
        creator="Jane Doe",
        source_title="Panorama",
        publisher="BBC",
        published_date="2019-03",
        license="CC BY 4.0",
    )
    assert compose_credit(asset) == "Jane Doe — Panorama — BBC — 2019-03 — CC BY 4.0"


def test_a_single_field_gets_no_separator():
    """The failure mode a naive join produces: " — BBC" or "BBC — "."""
    assert compose_credit(attributed(publisher="BBC")) == "BBC"


def test_missing_fields_are_skipped_not_padded():
    asset = attributed(creator="Jane Doe", publisher="BBC")
    assert compose_credit(asset) == "Jane Doe — BBC"


def test_nothing_recorded_composes_to_empty_string():
    """Not " — — — — ", and not None: the UI renders this straight into a field."""
    assert compose_credit(attributed()) == ""


def test_whitespace_only_fields_count_as_missing():
    asset = attributed(creator="   ", publisher="BBC")
    assert compose_credit(asset) == "BBC"


def test_values_are_stripped():
    assert compose_credit(attributed(publisher="  BBC  ")) == "BBC"


def test_credit_line_is_not_part_of_the_composition():
    """It is what the composition is an alternative *to*, not an input to it."""
    asset = attributed(publisher="BBC", credit_line="Something else entirely")
    assert compose_credit(asset) == "BBC"


# ─── the override ────────────────────────────────────────────────────────────


def test_the_override_wins_over_the_composition():
    resolved = resolve(attributed(publisher="BBC", credit_line="Courtesy of the BBC"))
    assert resolved.credit == "Courtesy of the BBC"


def test_without_an_override_the_composition_is_used():
    resolved = resolve(attributed(creator="Jane Doe", publisher="BBC"))
    assert resolved.credit == "Jane Doe — BBC"


def test_a_blank_override_falls_back_rather_than_blanking_the_credit():
    resolved = resolve(attributed(publisher="BBC", credit_line="   "))
    assert resolved.credit == "BBC"


# ─── inheritance ─────────────────────────────────────────────────────────────


def test_a_clip_with_nothing_of_its_own_inherits_every_field():
    parent = attributed(creator="Jane Doe", publisher="BBC", source_title="Panorama")
    resolved = resolve(attributed(), parent)

    assert resolved.creator == "Jane Doe"
    assert resolved.publisher == "BBC"
    assert set(resolved.inherited) == {"creator", "publisher", "source_title"}


def test_an_override_is_per_field_not_all_or_nothing():
    """The whole reason inheritance coalesces field by field."""
    parent = attributed(creator="Jane Doe", publisher="BBC", source_title="Panorama")
    clip = attributed(creator="Someone Else")
    resolved = resolve(clip, parent)

    assert resolved.creator == "Someone Else"
    assert resolved.publisher == "BBC"
    assert "creator" not in resolved.inherited
    assert "publisher" in resolved.inherited


def test_correcting_the_parent_changes_what_the_clip_resolves_to():
    """Resolution happens on read, so there is no copy anywhere to go stale.

    This is the property that made copy-on-create the wrong design: a correction has to
    reach the clips already cut from it.
    """
    parent = attributed(publisher="BBC Two")
    clip = attributed()
    assert resolve(clip, parent).publisher == "BBC Two"

    parent.publisher = "BBC Four"
    assert resolve(clip, parent).publisher == "BBC Four"


def test_a_blank_field_on_the_clip_still_inherits():
    parent = attributed(publisher="BBC")
    assert resolve(attributed(publisher="  "), parent).publisher == "BBC"


def test_no_parent_resolves_to_the_asset_alone():
    resolved = resolve(attributed(publisher="BBC"))
    assert resolved.publisher == "BBC"
    assert resolved.inherited == ()


def test_a_parent_with_nothing_recorded_inherits_nothing():
    resolved = resolve(attributed(), attributed())
    assert resolved.inherited == ()
    assert resolved.is_empty


def test_retrieved_at_inherits_as_a_datetime():
    """The one non-string field: blankness is not emptiness for a datetime."""
    when = datetime(2026, 1, 1, 12, 0, 0)
    resolved = resolve(attributed(), attributed(retrieved_at=when))
    assert resolved.retrieved_at == when
    assert "retrieved_at" in resolved.inherited


# ─── is_empty, which is what the unattributed filter means ───────────────────


def test_is_empty_is_true_only_when_nothing_at_all_is_recorded():
    assert resolve(attributed()).is_empty
    assert not resolve(attributed(source_url="https://example.org")).is_empty
    assert not resolve(attributed(retrieved_at=datetime(2026, 1, 1))).is_empty


def test_an_inheriting_clip_is_not_empty():
    """It has attribution — it just did not type it itself."""
    assert not resolve(attributed(), attributed(publisher="BBC")).is_empty


# ─── the search text ─────────────────────────────────────────────────────────


def test_index_text_carries_the_who_and_the_where():
    resolved = resolve(
        attributed(
            creator="Jane Doe",
            publisher="BBC",
            source_title="Panorama",
            license="CC BY 4.0",
            source_url="https://example.org/x",
        )
    )
    text = index_text(resolved)
    for term in ("Jane Doe", "BBC", "Panorama", "CC BY 4.0", "https://example.org/x"):
        assert term in text


def test_index_text_leaves_dates_out():
    """Both are served exactly by the date-range filters; a bare year in a text index
    mostly collides with titles instead of helping."""
    resolved = resolve(
        attributed(published_date="1994", retrieved_at=datetime(2026, 1, 1), publisher="BBC")
    )
    assert index_text(resolved) == "BBC"


def test_index_text_of_nothing_is_empty():
    assert index_text(resolve(attributed())) == ""


def test_a_clip_is_indexed_under_what_it_inherited():
    """Otherwise "everything from the BBC" is quietly missing every clip."""
    resolved = resolve(attributed(), attributed(publisher="BBC"))
    assert "BBC" in index_text(resolved)


# ─── the two copies of the field list ────────────────────────────────────────


def test_the_migration_indexes_the_same_fields_this_module_does():
    """`9c2e08b4a1f7` carries a literal copy of the attribution_text expression.

    That duplication is deliberate — a migration has to describe the schema as it was
    when it was written, so it cannot import this module. This is what stops the two
    drifting: if a field is added here and not there, an existing library is repopulated
    without it and stays unsearchable by that field until every asset is edited again.
    """
    migration = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "20260920_0731_rebuild_asset_fts_with_attribution.py"
    ).read_text()
    expression = migration.split("TRIM(")[1].split("FROM asset")[0]

    for name in ATTRIBUTION_TEXT_FIELDS:
        assert f"a.{name}" in expression, f"the migration does not index {name}"

    for name in ("published_date", "retrieved_at"):
        assert f"a.{name}" not in expression, f"the migration indexes {name}, this module does not"
