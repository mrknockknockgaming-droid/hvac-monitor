import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hvaccloud.db import Account, ApiKey, System, init_db, session_factory  # noqa: E402
from hvaccloud.refrigerants import Tables  # noqa: E402


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    init_db(engine)
    return session_factory(engine)


@pytest.fixture(scope="session")
def tables():
    return Tables(lambda *a: None)


@pytest.fixture
def seeded(sessions):
    """Two accounts; account 1 owns system 'home' (R-410A, 14.0 psia), account 2 owns 'other'."""
    with sessions() as s, s.begin():
        a1, a2 = Account(name="Tyler", email="t@example.com"), Account(name="Other", email="o@example.com")
        s.add_all([a1, a2])
        s.flush()
        s.add_all([System(account_id=a1.id, name="Home", site_id="home", atm_psia=14.0),
                   System(account_id=a2.id, name="Other", site_id="other")])
        k1, key1 = ApiKey.new(a1.id)
        k2, key2 = ApiKey.new(a2.id)
        s.add_all([k1, k2])
    return {"key1": key1, "key2": key2}
