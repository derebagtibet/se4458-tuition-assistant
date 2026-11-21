"""Unit-test fixtures. Tests run against an in-memory SQLite DB through the same SQLAlchemy models,
so they need neither the cloud deployment, PostgreSQL, the gateway nor a live LLM.
(PostgreSQL is the deployment database; SQLite is used only as a test double.)"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789")

from app.db import Base, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        seed(s)
    yield factory
    engine.dispose()


@pytest.fixture()
def client(db_session_factory):
    app = create_app(init_db=False)

    def _get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c


def _token(client: TestClient, username: str, password: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/token", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def student_headers(client):
    return _token(client, "s1001", "Passw0rd!")


@pytest.fixture()
def admin_headers(client):
    return _token(client, "admin", "Admin123!")


@pytest.fixture()
def bank_headers(client):
    return _token(client, "bankapp", "Bank123!")
