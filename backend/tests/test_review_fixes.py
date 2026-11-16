"""Regression tests for issues found in the pre-push code review."""
import threading
import time

import pytest

import db

SET_ROLE = "mutation($id: Int!, $role: String!) { setUserRole(id: $id, role: $role) { role } }"
HISTORY = "query($id: Int!) { alertHistory(id: $id) { action changes { field to } } }"


def _slow_user_lookup(monkeypatch, delay=0.2):
    """Widen the window between reading a user and writing, to expose check-then-act races."""
    real = db._user

    def slow(conn, user_id):
        row = real(conn, user_id)
        time.sleep(delay)
        return row
    monkeypatch.setattr(db, "_user", slow)


def _run_concurrently(*fns):
    results = [None] * len(fns)

    def run(i, fn):
        try:
            results[i] = fn()
        except Exception as e:  # noqa: BLE001 - collected for assertions
            results[i] = e
    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class TestRaces:
    def test_two_admins_demoting_each_other_leaves_one_admin(self, monkeypatch):
        a = db.create_user("a@test.io", "A", "password123", role="Admin")
        b = db.create_user("b@test.io", "B", "password123", role="Admin")
        _slow_user_lookup(monkeypatch)
        results = _run_concurrently(
            lambda: db.set_user_role(b["id"], "Viewer", a),
            lambda: db.set_user_role(a["id"], "Viewer", b),
        )
        admins = [u for u in db.list_users() if u["role"] == "Admin"]
        assert len(admins) == 1, results
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 1 and str(errors[0]) == "Cannot remove the last admin"

    def test_simultaneous_first_signups_make_only_one_admin(self, monkeypatch):
        real_hash = db.hash_password

        def slow_hash(pw, iterations=None):
            time.sleep(0.2)  # hashing happens between the "first user?" check and the insert
            return real_hash(pw, iterations)
        monkeypatch.setattr(db, "hash_password", slow_hash)
        _run_concurrently(
            lambda: db.create_user("one@test.io", "One", "password123"),
            lambda: db.create_user("two@test.io", "Two", "password123"),
        )
        assert sorted(u["role"] for u in db.list_users()) == ["Admin", "Pending"]

    def test_duplicate_signup_race_gives_friendly_error(self, monkeypatch):
        real_hash = db.hash_password
        monkeypatch.setattr(db, "hash_password", lambda pw, it=None: (time.sleep(0.2), real_hash(pw, it))[1])
        results = _run_concurrently(
            lambda: db.create_user("dup@test.io", "D", "password123"),
            lambda: db.create_user("dup@test.io", "D", "password123"),
        )
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 1
        assert isinstance(errors[0], db.AuthError)
        assert str(errors[0]) == "An account with this email already exists"


class TestDeletedAlertHistory:
    def test_viewer_cannot_read_history_of_deleted_alert(self, gql, anon_gql, token_for):
        gql("mutation { deleteAlert(id: 1) { id } }")
        body = anon_gql(HISTORY, {"id": 1}, token=token_for("Viewer"))
        assert body["errors"][0]["message"] == "Alert 1 not found"

    def test_auditor_still_can(self, gql, anon_gql, token_for):
        gql("mutation { deleteAlert(id: 1) { id } }")
        history = anon_gql(HISTORY, {"id": 1}, token=token_for("Auditor"))["data"]["alertHistory"]
        assert history[0]["action"] == "alert.delete"

    def test_viewer_can_read_history_of_existing_alert(self, gql, anon_gql, token_for):
        gql('mutation { updateAlert(id: 1, input: {notes: "x"}) { alert { id } } }')
        assert anon_gql(HISTORY, {"id": 1}, token=token_for("Viewer"))["data"]["alertHistory"]

    def test_rest_same_rule(self, client, anon_client, token_for):
        client.delete("/api/alerts/1")
        viewer = {"Authorization": f"Bearer {token_for('Viewer')}"}
        auditor = {"Authorization": f"Bearer {token_for('Auditor')}"}
        assert anon_client.get("/api/alerts/1/history", headers=viewer).status_code == 404
        assert anon_client.get("/api/alerts/1/history", headers=auditor).status_code == 200


class TestRestValidation:
    @pytest.mark.parametrize("field", ["status", "severity"])
    def test_null_status_or_severity_is_422_not_500(self, client, field):
        res = client.patch("/api/alerts/1", json={field: None})
        assert res.status_code == 422
        assert db.get_alert(1)[field] is not None

    def test_null_notes_still_allowed(self, client):
        assert client.patch("/api/alerts/1", json={"notes": None}).json()["notes"] is None

    @pytest.mark.parametrize("field", ["client_name", "address", "country", "raised_by"])
    def test_blank_required_fields_rejected(self, client, new_alert, field):
        new_alert[field] = "   "
        assert client.post("/api/alerts", json=new_alert).status_code == 422

    def test_required_fields_are_trimmed(self, client, new_alert):
        new_alert["client_name"] = "  Padded Name  "
        assert client.post("/api/alerts", json=new_alert).json()["client_name"] == "Padded Name"


class TestSignInHardening:
    def test_unknown_email_still_runs_password_hash(self, monkeypatch):
        db.create_user("real@test.io", "R", "password123")
        calls = []
        real = db.verify_password
        monkeypatch.setattr(db, "verify_password", lambda pw, stored: calls.append(stored) or real(pw, stored))
        with pytest.raises(db.AuthError):
            db.authenticate("nobody@test.io", "password123")
        assert len(calls) == 1  # same work as a wrong password for a real account

    @pytest.mark.parametrize("field,value,message", [
        ("email", "a" * 250 + "@x.io", "Email is too long"),
        ("name", "n" * 101, "Name is too long"),
        ("password", "p" * 1025, "Password is too long"),
    ], ids=["email", "name", "password"])
    def test_signup_length_limits(self, field, value, message):
        args = {"email": "ok@test.io", "name": "Ok", "password": "password123", field: value}
        with pytest.raises(db.AuthError, match=message):
            db.create_user(**args)

    def test_huge_failed_signin_is_truncated_in_audit_log(self):
        with pytest.raises(db.AuthError):
            db.authenticate("x" * 1_000_000, "p" * 1_000_000)
        [entry] = db.list_audit(action="auth.sign_in_failed")[0]
        assert len(entry["changes"]["email"][1]) <= db.MAX_EMAIL_LENGTH


class TestGraphQLWebSocketsDisabled:
    """The schema has no subscriptions, so /graphql must refuse WebSocket upgrades
    (closes the legacy graphql-ws auth bypass and subscription DoS advisories)."""

    @pytest.mark.parametrize("protocol", ["graphql-transport-ws", "graphql-ws"])
    def test_websocket_upgrade_is_refused(self, anon_client, protocol):
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc:
            with anon_client.websocket_connect("/graphql", subprotocols=[protocol]) as ws:
                ws.receive_text()
        assert exc.value.code == 4406  # Strawberry: "Subprotocol not acceptable"
