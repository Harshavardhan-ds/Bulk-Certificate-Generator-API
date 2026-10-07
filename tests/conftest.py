import os
import shutil
import tempfile

# must be set before the app is imported
_tmp = tempfile.mkdtemp(prefix="certtests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["CERTIFICATE_DIR"] = f"{_tmp}/certs"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import CERTIFICATE_DIR  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(CERTIFICATE_DIR, ignore_errors=True)
    CERTIFICATE_DIR.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def payload():
    return {
        "event_name": "Python Bootcamp 2026",
        "issued_by": "Acme Academy",
        "issue_date": "2026-05-20",
        "recipients": [
            {"name": "Asha Verma", "email": "asha@example.com"},
            {"name": "Ben Carter", "email": "ben@example.com"},
            {"name": "Chitra Rao", "email": "chitra@example.com"},
        ],
    }
