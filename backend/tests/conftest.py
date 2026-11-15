"""Shared fixtures. Every test gets its own throwaway SQLite file,
so tests never touch backend/alerts.db or a running dev server."""
import pytest
from fastapi.testclient import TestClient

import db
from main import app


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "test_alerts.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "PBKDF2_ITERATIONS", 1_000)  # keep tests fast
    db.init_db()
    return path


@pytest.fixture
def token_for():
    """Create a user with the given role and return a valid session token."""
    def make(role="Admin", email=None):
        user = db.create_user(email or f"{role.lower()}@test.io", role, "password123", role=role)
        return db.create_session(user["id"])
    return make


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def anon_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(token_for):
    """Signed in as an Admin (all permissions)."""
    with TestClient(app, headers=_auth(token_for("Admin"))) as c:
        yield c


def _gql_runner(c, default_token=None):
    def run(query, variables=None, token=default_token):
        headers = _auth(token) if token else {}
        res = c.post("/graphql", json={"query": query, "variables": variables or {}}, headers=headers)
        assert res.status_code == 200, res.text
        return res.json()
    return run


@pytest.fixture
def gql(client):
    """Run a GraphQL operation as an Admin; pass token=... to act as someone else."""
    return _gql_runner(client)


@pytest.fixture
def anon_gql(anon_client):
    """Run a GraphQL operation with no session (or pass token=...)."""
    return _gql_runner(anon_client)


@pytest.fixture
def new_alert():
    """Valid snake_case alert data for the db/REST layers."""
    return {
        "client_name": "Test Client",
        "dob": "1990-01-15",
        "address": "1 Test Street",
        "country": "Testland",
        "alert_type": "PEP",
        "severity": "High",
        "raised_by": "Tester",
        "notes": "created in test",
    }
