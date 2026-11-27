from contextlib import closing

import pytest
from deploy import Deployment

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


@pytest.fixture
def make(tmp_path):
    """Build a deployment of the demo queue at a chosen planted rate."""
    created = []

    def build(rate=0.5):
        deployment = Deployment(tmp_path, rate)
        created.append(deployment)
        return deployment

    yield build
    for deployment in created:
        deployment.close()


@pytest.fixture
def deployment(make):
    return make()


@pytest.fixture
def ov(deployment):
    """An open queue with all six example planted errors approved."""
    ov = deployment.open()
    deployment.load_planted(ov)
    return ov


@pytest.fixture
def no_proxy(monkeypatch):
    """Keep test requests to the local stand-in off any system proxy."""
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
