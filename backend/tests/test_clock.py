"""The timestamp convention, and a guard that keeps it.

Naive UTC is not a style preference here — `jobs.runner.is_stale` subtracts the current
time from a value loaded out of the database, and Python raises TypeError on mixing aware
with naive rather than returning a wrong answer. So a single aware datetime reaching a
timestamp column breaks the stale sweeper, which is the thing that stops a dead worker
holding a job at "processing" forever.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.clock import utcnow

APP = Path(__file__).resolve().parent.parent / "app"


def test_it_is_naive():
    assert utcnow().tzinfo is None


def test_it_is_actually_utc():
    """Naive is only correct if it is naive *UTC*. A local-time clock would also be
    naive and would be wrong by the machine's offset — invisible in a UTC container and
    silently broken on a developer's laptop."""
    drift = abs((utcnow() - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
    assert drift < 1


def test_it_subtracts_against_a_value_from_the_database():
    """What is_stale does. An aware implementation raises TypeError here instead."""
    stored = datetime(2020, 1, 1, 12, 0, 0)  # as SQLite hands it back
    assert utcnow() - stored > timedelta(days=1)


@pytest.mark.parametrize(
    "path", sorted(p for p in APP.rglob("*.py") if p.name != "clock.py")
)
def test_no_module_calls_datetime_utcnow(path):
    """`datetime.utcnow()` is deprecated from 3.12 and scheduled for removal, so a new
    call site is a future outage, not a warning. clock.py is exempt because its docstring
    names the function it exists to replace."""
    assert "datetime.utcnow" not in path.read_text(), (
        f"{path.relative_to(APP.parent)} calls datetime.utcnow(); use app.clock.utcnow"
    )
