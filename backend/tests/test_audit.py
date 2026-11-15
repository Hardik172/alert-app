"""Audit trail: every change is recorded atomically, append-only, and readable via GraphQL / REST / CSV."""
import csv
import io
import sqlite3

import pytest

import db

ENTRY = "id at actorId actorName action entityType entityId changes { field from to } snapshot"
HISTORY = "query($id: Int!) { alertHistory(id: $id) { %s } }" % ENTRY
UPDATE = """mutation($id: Int!, $input: UpdateAlertInput!) {
  updateAlert(id: $id, input: $input) { alert { id } auditEntry { %s } }
}""" % ENTRY
CREATE = """mutation($input: CreateAlertInput!) {
  createAlert(input: $input) { alert { id } auditEntry { %s } }
}""" % ENTRY
DELETE = "mutation($id: Int!) { deleteAlert(id: $id) { id auditEntry { %s } } }" % ENTRY
AUDIT_LOG = """query($filter: AuditFilter, $limit: Int, $offset: Int) {
  auditLog(filter: $filter, limit: $limit, offset: $offset) { total entries { action actorName entityId } }
}"""


@pytest.fixture
def admin():
    return db.create_user("ada@test.io", "Ada Admin", "password123", role="Admin")


@pytest.fixture
def create_input():
    return {"clientName": "Audit Client", "dob": "1985-05-05", "address": "1 Ledger Lane",
            "country": "Testland", "alertType": "AML", "raisedBy": "Ada"}


def audit_rows(**filters):
    return db.list_audit(**filters, limit=500)[0]


def changes(entry):
    return {c["field"]: [c["from"], c["to"]] for c in entry["changes"]}


# ---------- db layer ----------

class TestRecording:
    def test_update_records_only_real_changes(self, admin):
        _, audit_id = db.update_alert(1, {"status": "Closed", "severity": "High", "notes": "done"}, admin)
        entry = db.get_audit(audit_id)
        assert entry["action"] == "alert.update"
        assert (entry["actor_id"], entry["actor_name"]) == (admin["id"], "Ada Admin")
        assert (entry["entity_type"], entry["entity_id"]) == ("alert", 1)
        # severity was already High -> not recorded
        assert entry["changes"] == {
            "status": ["Open", "Closed"],
            "notes": ["Multiple cash deposits just under reporting threshold.", "done"],
        }

    def test_noop_update_writes_nothing(self, admin):
        before = len(audit_rows())
        row, audit_id = db.update_alert(1, {"status": "Open"}, admin)
        assert audit_id is None and row["status"] == "Open"
        assert len(audit_rows()) == before

    def test_create_records_initial_values(self, admin, new_alert):
        row, audit_id = db.create_alert(new_alert, admin)
        entry = db.get_audit(audit_id)
        assert entry["action"] == "alert.create" and entry["entity_id"] == row["id"]
        assert entry["changes"]["client_name"] == [None, "Test Client"]
        assert entry["changes"]["status"] == [None, "Open"]

    def test_delete_keeps_full_snapshot(self, admin):
        original = db.get_alert(3)
        entry = db.get_audit(db.delete_alert(3, admin))
        assert entry["action"] == "alert.delete"
        assert entry["snapshot"] == original
        # history remains queryable after the alert is gone
        assert [e["action"] for e in audit_rows(entity_type="alert", entity_id=3)] == ["alert.delete"]

    def test_role_change(self, admin):
        viewer = db.create_user("v@test.io", "Vee", "password123")
        db.set_user_role(viewer["id"], "Auditor", admin)
        [entry] = audit_rows(action="user.role_change")
        assert entry["changes"] == {"role": ["Viewer", "Auditor"]}
        assert entry["snapshot"] == {"email": "v@test.io", "name": "Vee"}
        assert entry["actor_name"] == "Ada Admin"

    def test_same_role_is_not_recorded(self, admin):
        db.set_user_role(admin["id"], "Admin", admin)
        assert audit_rows(action="user.role_change") == []

    def test_auth_events_never_store_passwords(self, admin):
        db.authenticate("ada@test.io", "password123")
        with pytest.raises(db.AuthError):
            db.authenticate("ADA@test.io", "wrong-password-xyz")
        with pytest.raises(db.AuthError):
            db.authenticate("ghost@test.io", "whatever-pw")
        token = db.create_session(admin["id"])
        db.delete_session(token)

        actions = [e["action"] for e in reversed(audit_rows(entity_type="user"))]
        assert actions == ["auth.sign_up", "auth.sign_in", "auth.sign_in_failed", "auth.sign_in_failed", "auth.sign_out"]
        failed = audit_rows(action="auth.sign_in_failed")
        assert {e["changes"]["email"][1] for e in failed} == {"ada@test.io", "ghost@test.io"}
        assert all(e["actor_id"] is None and e["actor_name"] == "System" for e in failed)

        with db.get_db() as conn:
            dump = " ".join(str(v) for r in conn.execute("SELECT * FROM audit_log") for v in tuple(r))
        assert "wrong-password-xyz" not in dump and "password123" not in dump

    def test_change_and_audit_share_a_transaction(self, admin, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("audit write failed")
        monkeypatch.setattr(db, "_audit", boom)
        with pytest.raises(RuntimeError):
            db.update_alert(1, {"status": "Closed"}, admin)
        assert db.get_alert(1)["status"] == "Open"  # rolled back


class TestAppendOnly:
    @pytest.mark.parametrize("sql", ["UPDATE audit_log SET actor_name = 'x'", "DELETE FROM audit_log"])
    def test_rows_cannot_be_changed_or_removed(self, admin, sql):
        db.update_alert(1, {"notes": "n"}, admin)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            with db.get_db() as conn:
                conn.execute(sql)
        assert len(audit_rows()) >= 2


class TestListAudit:
    def test_filters_and_pagination(self, admin):
        for i in range(5):
            db.update_alert(1, {"notes": f"n{i}"}, admin)
        db.update_alert(2, {"notes": "other"}, None)

        rows, total = db.list_audit(entity_type="alert", entity_id=1, limit=2, offset=1)
        assert total == 5 and [r["changes"]["notes"][1] for r in rows] == ["n3", "n2"]
        assert db.list_audit(actor="ada")[1] == 5 + 1  # 5 updates + sign_up
        assert [r["actor_name"] for r in audit_rows(entity_id=2, entity_type="alert")] == ["System"]
        assert db.list_audit(since="2000-01-01", until="2000-12-31")[1] == 0
        today = db.get_audit(1)["at"][:10]
        assert db.list_audit(since=today, until=today)[1] == len(audit_rows())


# ---------- GraphQL ----------

class TestGraphQL:
    def test_mutations_return_their_audit_entry(self, gql, create_input):
        created = gql(CREATE, {"input": create_input})["data"]["createAlert"]
        assert created["auditEntry"]["action"] == "alert.create"
        assert created["auditEntry"]["actorName"] == "Admin"  # token_for("Admin") user name

        updated = gql(UPDATE, {"id": 1, "input": {"status": "Closed"}})["data"]["updateAlert"]["auditEntry"]
        assert updated["action"] == "alert.update"
        assert changes(updated) == {"status": ["Open", "Closed"]}

        noop = gql(UPDATE, {"id": 1, "input": {"status": "Closed"}})["data"]["updateAlert"]
        assert noop["auditEntry"] is None

        deleted = gql(DELETE, {"id": 1})["data"]["deleteAlert"]["auditEntry"]
        assert deleted["action"] == "alert.delete" and deleted["snapshot"]["client_name"] == "John Smith"

    def test_history_is_newest_first(self, gql):
        gql(UPDATE, {"id": 2, "input": {"status": "Closed"}})
        gql(UPDATE, {"id": 2, "input": {"notes": "wrapped up"}})
        history = gql(HISTORY, {"id": 2})["data"]["alertHistory"]
        assert [list(changes(e)) for e in history] == [["notes"], ["status"]]

    def test_forbidden_mutation_leaves_no_audit_entry(self, anon_gql, token_for):
        before = len(audit_rows())
        body = anon_gql(UPDATE, {"id": 1, "input": {"status": "Closed"}}, token=token_for("Viewer"))
        assert body["errors"][0]["extensions"]["code"] == "FORBIDDEN"
        assert len(audit_rows()) == before + 1  # only the Viewer's sign_up from token_for

    def test_deleted_alert_history_needs_audit_read(self, gql, anon_gql, token_for):
        # The create entry and the snapshot both hold the deleted record's values,
        # so without audit:read the whole history is hidden, not just the snapshot.
        gql(DELETE, {"id": 1})
        as_viewer = anon_gql(HISTORY, {"id": 1}, token=token_for("Viewer"))
        as_auditor = anon_gql(HISTORY, {"id": 1}, token=token_for("Auditor"))["data"]["alertHistory"]
        assert as_viewer["data"] is None and as_viewer["errors"][0]["message"] == "Alert 1 not found"
        assert as_auditor[0]["snapshot"]["client_name"] == "John Smith"

    def test_snapshot_field_is_gated_even_if_reachable(self):
        # Defence in depth: the field resolver itself hides snapshots from non-auditors.
        from types import SimpleNamespace
        from schema import AuditEntry
        entry = AuditEntry.from_row({"id": 1, "at": "x", "actor_id": None, "actor_name": "S", "action": "alert.delete",
                                     "entity_type": "alert", "entity_id": 1, "changes": {}, "snapshot": {"a": 1}})
        viewer = SimpleNamespace(context={"user": {"role": "Viewer"}})
        auditor = SimpleNamespace(context={"user": {"role": "Auditor"}})
        assert entry.snapshot(viewer) is None
        assert entry.snapshot(auditor) == {"a": 1}

    def test_audit_log_filters(self, gql):
        gql(UPDATE, {"id": 1, "input": {"notes": "a"}})
        gql(UPDATE, {"id": 2, "input": {"notes": "b"}})
        page = gql(AUDIT_LOG, {"filter": {"action": "alert.update"}, "limit": 1})["data"]["auditLog"]
        assert page["total"] == 2 and page["entries"] == [{"action": "alert.update", "actorName": "Admin", "entityId": 2}]
        body = gql(AUDIT_LOG, {"filter": {"action": "alert.explode"}})
        assert "Invalid action 'alert.explode'" in body["errors"][0]["message"]

    def test_meta_lists_audit_actions_and_auditor_role(self, gql):
        meta = gql("{ meta { roles auditActions } }")["data"]["meta"]
        assert "Auditor" in meta["roles"]
        assert meta["auditActions"] == db.AUDIT_ACTIONS


# ---------- REST ----------

class TestREST:
    def test_rest_writes_are_audited_with_the_caller(self, client):
        client.patch("/api/alerts/1", json={"status": "Closed"})
        client.delete("/api/alerts/2")
        history = client.get("/api/alerts/1/history").json()
        assert history[0]["action"] == "alert.update" and history[0]["actor_name"] == "Admin"
        assert history[0]["changes"] == {"status": ["Open", "Closed"]}

        page = client.get("/api/audit", params={"entity_type": "alert"}).json()
        assert page["total"] == 2
        assert [e["action"] for e in page["entries"]] == ["alert.delete", "alert.update"]
        assert client.get("/api/audit", params={"action": "nope"}).status_code == 422

    def test_csv_export(self, client):
        db.update_alert(1, {"notes": "=HYPERLINK(\"http://evil\")"}, None)
        client.patch("/api/alerts/2", json={"status": "Closed"})
        res = client.get("/api/audit.csv", params={"entity_type": "alert"})
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert "attachment" in res.headers["content-disposition"]

        rows = list(csv.DictReader(io.StringIO(res.text)))
        assert [r["action"] for r in rows] == ["alert.update", "alert.update"]
        assert rows[0]["actor_name"] == "Admin"
        assert '"status": ["Under Review", "Closed"]' in rows[0]["changes"]
        assert "=HYPERLINK" in rows[1]["changes"] and rows[1]["changes"].startswith("{")  # JSON, not a formula

    def test_csv_neutralises_formula_cells(self):
        from main import _csv_cell
        assert _csv_cell("=1+1") == "'=1+1"
        assert _csv_cell("@SUM(A1)") == "'@SUM(A1)"
        assert _csv_cell("Ada") == "Ada"
        assert _csv_cell(None) == ""
