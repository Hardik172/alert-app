"""Sign up / sign in / sessions and role-based access control (GraphQL + REST)."""
import pytest

import db

SIGN_UP = """mutation($input: SignUpInput!) {
  signUp(input: $input) { token user { id email name role permissions } }
}"""
SIGN_IN = """mutation($email: String!, $password: String!) {
  signIn(email: $email, password: $password) { token user { email role } }
}"""
ME = "{ me { email name role permissions } }"
SET_ROLE = "mutation($id: Int!, $role: String!) { setUserRole(id: $id, role: $role) { id role permissions } }"


def sign_up(anon_gql, email="ana@test.io", name="Ana", password="password123"):
    return anon_gql(SIGN_UP, {"input": {"email": email, "name": name, "password": password}})


def error(body):
    assert body.get("errors"), f"expected an error, got {body}"
    return body["errors"][0]


# ---------- db layer ----------

class TestPasswords:
    def test_hash_is_salted_and_verifiable(self):
        a, b = db.hash_password("secret-pw"), db.hash_password("secret-pw")
        assert a != b and "secret-pw" not in a
        assert db.verify_password("secret-pw", a)
        assert not db.verify_password("wrong", a)

    def test_malformed_hash_does_not_verify(self):
        assert not db.verify_password("x", "garbage")

    def test_session_tokens_are_stored_hashed(self, temp_db):
        user = db.create_user("a@b.io", "A", "password123")
        token = db.create_session(user["id"])
        with db.get_db() as conn:
            stored = conn.execute("SELECT token_hash FROM sessions").fetchone()[0]
        assert stored != token
        assert db.user_for_token(token)["email"] == "a@b.io"

    def test_expired_session_is_rejected(self, monkeypatch):
        from datetime import timedelta
        monkeypatch.setattr(db, "SESSION_TTL", timedelta(seconds=-1))
        user = db.create_user("a@b.io", "A", "password123")
        assert db.user_for_token(db.create_session(user["id"])) is None


# ---------- GraphQL: sign up / sign in ----------

class TestSignUp:
    def test_first_user_is_admin_then_pending(self, anon_gql):
        first = sign_up(anon_gql)["data"]["signUp"]
        assert first["user"]["role"] == "Admin"
        assert "user:manage" in first["user"]["permissions"]
        assert first["token"]

        second = sign_up(anon_gql, email="bo@test.io", name="Bo")["data"]["signUp"]
        # Open sign-up must not grant data access: later users wait for an Admin.
        assert second["user"]["role"] == "Pending"
        assert second["user"]["permissions"] == []

    def test_pending_user_sees_only_themself_until_promoted(self, anon_gql):
        admin = sign_up(anon_gql)["data"]["signUp"]["token"]
        bo = sign_up(anon_gql, email="bo@test.io", name="Bo")["data"]["signUp"]
        me = anon_gql("{ me { email role permissions } }", token=bo["token"])["data"]["me"]
        assert me == {"email": "bo@test.io", "role": "Pending", "permissions": []}
        assert error(anon_gql(ALERTS, token=bo["token"]))["extensions"]["code"] == "FORBIDDEN"

        set_role = 'mutation($id: Int!) { setUserRole(id: $id, role: "Viewer") { role } }'
        assert anon_gql(set_role, {"id": int(bo["user"]["id"])}, token=admin)["data"]["setUserRole"]["role"] == "Viewer"
        assert not anon_gql(ALERTS, token=bo["token"]).get("errors")  # same session, access now granted

    def test_role_cannot_be_chosen_by_client(self, anon_gql):
        body = anon_gql("""mutation { signUp(input: {email: "x@y.io", name: "X", password: "password123", role: "Admin"}) { token } }""")
        assert "role" in error(body)["message"]

    def test_email_is_normalised_and_unique(self, anon_gql):
        sign_up(anon_gql, email="  Ana@Test.IO ")
        assert error(sign_up(anon_gql, email="ana@test.io"))["message"] == "An account with this email already exists"

    @pytest.mark.parametrize("field,value,message", [
        ("email", "not-an-email", "Enter a valid email address"),
        ("name", "   ", "Name is required"),
        ("password", "short", "Password must be at least 8 characters"),
    ])
    def test_validation(self, anon_gql, field, value, message):
        args = {"email": "ok@test.io", "name": "Ok", "password": "password123", field: value}
        assert error(sign_up(anon_gql, **args))["message"] == message


class TestSignIn:
    def test_sign_in_returns_a_working_token(self, anon_gql):
        sign_up(anon_gql)
        body = anon_gql(SIGN_IN, {"email": "ANA@test.io", "password": "password123"})
        token = body["data"]["signIn"]["token"]
        me = anon_gql(ME, token=token)["data"]["me"]
        assert me == {"email": "ana@test.io", "name": "Ana", "role": "Admin",
                      "permissions": ["alert:read", "alert:create", "alert:update", "alert:delete", "user:manage", "audit:read"]}

    @pytest.mark.parametrize("email,password", [("ana@test.io", "wrong-password"), ("nobody@test.io", "password123")])
    def test_bad_credentials_give_one_generic_error(self, anon_gql, email, password):
        sign_up(anon_gql)
        assert error(anon_gql(SIGN_IN, {"email": email, "password": password}))["message"] == "Invalid email or password"

    def test_me_is_null_when_signed_out(self, anon_gql):
        assert anon_gql(ME)["data"]["me"] is None
        assert anon_gql(ME, token="bogus")["data"]["me"] is None

    def test_sign_out_revokes_the_token(self, anon_gql):
        token = sign_up(anon_gql)["data"]["signUp"]["token"]
        assert anon_gql("mutation { signOut }", token=token)["data"]["signOut"] is True
        assert anon_gql(ME, token=token)["data"]["me"] is None


# ---------- GraphQL: RBAC ----------

ALERTS = "{ alerts { id } }"
CREATE = """mutation { createAlert(input: {clientName: "C", dob: "1990-01-01", address: "A",
  country: "X", alertType: "AML", raisedBy: "R"}) { alert { id } } }"""
UPDATE = 'mutation { updateAlert(id: 1, input: {notes: "n"}) { alert { notes } } }'
DELETE = "mutation { deleteAlert(id: 1) { id } }"
USERS = "{ users { email role } }"
HISTORY = "{ alertHistory(id: 1) { action } }"
AUDIT_LOG = "{ auditLog { total } }"

# operation -> roles allowed
MATRIX = {
    ALERTS: {"Viewer", "Analyst", "Auditor", "Admin"},
    "{ stats { total } }": {"Viewer", "Analyst", "Auditor", "Admin"},
    "{ meta { roles } }": {"Viewer", "Analyst", "Auditor", "Admin"},
    HISTORY: {"Viewer", "Analyst", "Auditor", "Admin"},
    CREATE: {"Analyst", "Admin"},
    UPDATE: {"Analyst", "Admin"},
    DELETE: {"Admin"},
    USERS: {"Admin"},
    AUDIT_LOG: {"Auditor", "Admin"},
    "{ alert(id: 1) { id } }": {"Viewer", "Analyst", "Auditor", "Admin"},
    # The main privilege-escalation path: only Admins may change roles.
    'mutation { setUserRole(id: 1, role: "Admin") { id } }': {"Admin"},
}


class TestGraphQLRBAC:
    @pytest.mark.parametrize("op", list(MATRIX))
    def test_anonymous_is_rejected(self, anon_gql, op):
        err = error(anon_gql(op))
        assert err["message"] == "Not signed in"
        assert err["extensions"]["code"] == "UNAUTHENTICATED"

    @pytest.mark.parametrize("role", ["Pending", "Viewer", "Analyst", "Auditor", "Admin"])
    @pytest.mark.parametrize("op", list(MATRIX))
    def test_permission_matrix(self, anon_gql, token_for, role, op):
        body = anon_gql(op, token=token_for(role))
        if role in MATRIX[op]:
            assert not body.get("errors"), body
        else:
            err = error(body)
            assert err["extensions"]["code"] == "FORBIDDEN"
            assert err["message"] == f"Your role ({role}) is not allowed to do this"

    def test_forbidden_mutation_changes_nothing(self, anon_gql, token_for):
        anon_gql(DELETE, token=token_for("Analyst"))
        assert len(anon_gql(ALERTS, token=token_for("Viewer", email="v@t.io"))["data"]["alerts"]) == 8


class TestUserManagement:
    def test_admin_promotes_user_and_it_takes_effect_immediately(self, anon_gql, token_for):
        admin = token_for("Admin")
        viewer_token = token_for("Viewer")
        viewer_id = db.user_for_token(viewer_token)["id"]
        assert error(anon_gql(CREATE, token=viewer_token))["extensions"]["code"] == "FORBIDDEN"

        updated = anon_gql(SET_ROLE, {"id": viewer_id, "role": "Analyst"}, token=admin)["data"]["setUserRole"]
        assert updated["role"] == "Analyst" and "alert:create" in updated["permissions"]
        assert not anon_gql(CREATE, token=viewer_token).get("errors")  # same session, new rights

    def test_list_users(self, gql, token_for):
        token_for("Viewer")
        roles = {u["email"]: u["role"] for u in gql(USERS)["data"]["users"]}
        assert roles == {"admin@test.io": "Admin", "viewer@test.io": "Viewer"}

    def test_cannot_remove_last_admin(self, anon_gql, token_for):
        admin = token_for("Admin")
        admin_id = db.user_for_token(admin)["id"]
        assert error(anon_gql(SET_ROLE, {"id": admin_id, "role": "Viewer"}, token=admin))["message"] == "Cannot remove the last admin"
        token_for("Admin", email="second@test.io")
        assert anon_gql(SET_ROLE, {"id": admin_id, "role": "Viewer"}, token=admin)["data"]["setUserRole"]["role"] == "Viewer"

    def test_invalid_role_and_missing_user(self, gql):
        assert "Invalid role 'Owner'" in error(gql(SET_ROLE, {"id": 1, "role": "Owner"}))["message"]
        assert error(gql(SET_ROLE, {"id": 999, "role": "Viewer"}))["message"] == "User 999 not found"


# ---------- REST ----------

class TestREST:
    def test_signup_signin_me_signout(self, anon_client):
        res = anon_client.post("/api/auth/signup", json={"email": "r@test.io", "name": "R", "password": "password123"})
        assert res.status_code == 201 and res.json()["user"]["role"] == "Admin"

        res = anon_client.post("/api/auth/signin", json={"email": "r@test.io", "password": "password123"})
        assert res.status_code == 200
        headers = {"Authorization": f"Bearer {res.json()['token']}"}
        assert anon_client.get("/api/auth/me", headers=headers).json()["email"] == "r@test.io"

        assert anon_client.post("/api/auth/signout", headers=headers).status_code == 204
        assert anon_client.get("/api/auth/me", headers=headers).status_code == 401

    def test_bad_signin_is_401_and_bad_signup_is_400(self, anon_client):
        assert anon_client.post("/api/auth/signin", json={"email": "x@y.io", "password": "nope-nope"}).status_code == 401
        assert anon_client.post("/api/auth/signup", json={"email": "x@y.io", "name": "X", "password": "short"}).status_code == 400

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/alerts"), ("get", "/api/alerts/1"), ("get", "/api/alerts/stats"),
        ("get", "/api/meta"), ("delete", "/api/alerts/1"), ("get", "/api/users"),
        ("get", "/api/alerts/1/history"), ("get", "/api/audit"), ("get", "/api/audit.csv"),
        ("post", "/api/alerts"), ("patch", "/api/users/1"),
    ])
    def test_anonymous_is_401(self, anon_client, method, path):
        assert getattr(anon_client, method)(path).status_code == 401

    @pytest.mark.parametrize("role,method,path,body,expected", [
        ("Pending", "get", "/api/alerts", None, 403),
        ("Pending", "get", "/api/alerts/1", None, 403),
        ("Pending", "get", "/api/meta", None, 403),
        ("Viewer", "get", "/api/alerts", None, 200),
        ("Viewer", "patch", "/api/alerts/1", {"notes": "x"}, 403),
        ("Analyst", "patch", "/api/alerts/1", {"notes": "x"}, 200),
        ("Analyst", "delete", "/api/alerts/1", None, 403),
        ("Admin", "delete", "/api/alerts/1", None, 204),
        ("Analyst", "get", "/api/users", None, 403),
        ("Admin", "get", "/api/users", None, 200),
        ("Viewer", "get", "/api/alerts/1/history", None, 200),
        ("Viewer", "get", "/api/audit", None, 403),
        ("Analyst", "get", "/api/audit.csv", None, 403),
        ("Auditor", "get", "/api/audit", None, 200),
        ("Auditor", "get", "/api/audit.csv", None, 200),
        ("Auditor", "patch", "/api/alerts/1", {"notes": "x"}, 403),
        ("Auditor", "delete", "/api/alerts/1", None, 403),
        ("Viewer", "post", "/api/alerts", "NEW_ALERT", 403),
        ("Auditor", "post", "/api/alerts", "NEW_ALERT", 403),
        ("Analyst", "post", "/api/alerts", "NEW_ALERT", 201),
        ("Viewer", "patch", "/api/users/1", {"role": "Admin"}, 403),
        ("Analyst", "patch", "/api/users/1", {"role": "Admin"}, 403),
        ("Auditor", "patch", "/api/users/1", {"role": "Admin"}, 403),
    ])
    def test_permissions(self, anon_client, token_for, new_alert, role, method, path, body, expected):
        body = new_alert if body == "NEW_ALERT" else body
        headers = {"Authorization": f"Bearer {token_for(role)}"}
        kwargs = {"headers": headers, **({"json": body} if body else {})}
        assert getattr(anon_client, method)(path, **kwargs).status_code == expected

    def test_admin_sets_role(self, client, token_for):
        token_for("Viewer")
        res = client.patch("/api/users/2", json={"role": "Analyst"})
        assert res.status_code == 200 and res.json()["permissions"] == ["alert:read", "alert:create", "alert:update"]
        assert client.patch("/api/users/2", json={"role": "Owner"}).status_code == 422
        assert client.patch("/api/users/99", json={"role": "Viewer"}).status_code == 404
