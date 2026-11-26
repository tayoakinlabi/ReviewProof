from contextlib import closing

import pytest

from reviewproof.store import create_oversight_db, create_planted_db
from reviewproof.store.eventlog import EventLog


@pytest.fixture
def oversight_path(tmp_path):
    return tmp_path / "oversight.db"


@pytest.fixture
def planted_path(tmp_path):
    return tmp_path / "planted.db"


@pytest.fixture
def oversight_db(oversight_path):
    with closing(create_oversight_db(oversight_path)) as db:
        yield db


@pytest.fixture
def registry_db(planted_path):
    with closing(create_planted_db(planted_path)) as db:
        yield db


@pytest.fixture
def event_log(oversight_db):
    return EventLog(oversight_db)
