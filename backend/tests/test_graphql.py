"""GraphQL API tests (schema.py via /graphql)."""
import pytest

STATS = "stats { total open underReview closed openHighSeverity }"

CREATE = """mutation($input: CreateAlertInput!) {
  createAlert(input: $input) { alert { id clientName dob status severity alertType } %s }
}""" % STATS

UPDATE = """mutation($id: Int!, $input: UpdateAlertInput!) {
  updateAlert(id: $id, input: $input) { alert { id status severity notes } %s }
}""" % STATS


@pytest.fixture
def create_input():
    return {
        "clientName": "Graph Client",
        "dob": "1985-05-05",
        "address": "2 Graph Road",
        "country": "Testland",
        "alertType": "Sanctions",
        "raisedBy": "Tester",
    }


def error_message(body):
    assert body.get("errors"), f"expected an error, got {body}"
    return body["errors"][0]["message"]


class TestQueries:
    def test_initial_load_returns_everything_in_one_request(self, gql):
        body = gql("""{
          alerts { id clientName dob address country alertType severity raisedBy status notes raisedAt }
          %s
          meta { alertTypes statuses severities }
        }""" % STATS)
        data = body["data"]
        assert len(data["alerts"]) == 8
        assert data["stats"] == {"total": 8, "open": 4, "underReview": 2, "closed": 2, "openHighSeverity": 3}
        assert data["meta"]["statuses"] == ["Open", "Under Review", "Closed"]
        first = data["alerts"][0]
        assert set(first) >= {"clientName", "dob", "address", "country", "alertType", "raisedBy"}

    def test_filters(self, gql):
        body = gql('{ alerts(filter: {alertType: "Fraud", status: "Under Review"}) { clientName } }')
        assert body["data"]["alerts"] == [{"clientName": "Chen Wei"}]

    def test_empty_string_filters_are_ignored(self, gql):
        body = gql('{ alerts(filter: {search: "", alertType: "", status: ""}) { id } }')
        assert len(body["data"]["alerts"]) == 8

    def test_invalid_filter_value_is_rejected(self, gql):
        body = gql('{ alerts(filter: {status: "Pending"}) { id } }')
        assert "Invalid status 'Pending'" in error_message(body)

    def test_stats_by_type(self, gql):
        body = gql("{ stats { byType { alertType count } } }")
        counts = {t["alertType"]: t["count"] for t in body["data"]["stats"]["byType"]}
        assert counts["AML"] == 2 and counts["Fraud"] == 2

    def test_alert_by_id(self, gql):
        body = gql("{ alert(id: 1) { clientName country } }")
        assert body["data"]["alert"] == {"clientName": "John Smith", "country": "United Kingdom"}

    def test_alert_not_found(self, gql):
        assert error_message(gql("{ alert(id: 999) { id } }")) == "Alert 999 not found"


class TestCreateAlert:
    def test_create_returns_alert_and_fresh_stats(self, gql, create_input):
        body = gql(CREATE, {"input": create_input})
        payload = body["data"]["createAlert"]
        assert payload["alert"]["clientName"] == "Graph Client"
        assert payload["alert"]["dob"] == "1985-05-05"
        assert payload["alert"]["status"] == "Open"
        assert payload["alert"]["severity"] == "Medium"  # default
        assert payload["stats"]["total"] == 9
        assert payload["stats"]["open"] == 5

    def test_created_alert_is_persisted(self, gql, create_input):
        new_id = gql(CREATE, {"input": create_input})["data"]["createAlert"]["alert"]["id"]
        assert gql("{ alert(id: %d) { clientName } }" % new_id)["data"]["alert"]["clientName"] == "Graph Client"

    def test_trims_whitespace(self, gql, create_input):
        create_input["clientName"] = "  Padded  "
        body = gql(CREATE, {"input": create_input})
        assert body["data"]["createAlert"]["alert"]["clientName"] == "Padded"

    @pytest.mark.parametrize("field,value,expected", [
        ("alertType", "Spam", "Invalid alertType 'Spam'"),
        ("severity", "Critical", "Invalid severity 'Critical'"),
        ("clientName", "   ", "clientName is required"),
        ("raisedBy", "", "raisedBy is required"),
    ])
    def test_validation(self, gql, create_input, field, value, expected):
        create_input[field] = value
        assert expected in error_message(gql(CREATE, {"input": create_input}))
        assert gql("{ stats { total } }")["data"]["stats"]["total"] == 8  # nothing written

    def test_rejects_bad_date(self, gql, create_input):
        create_input["dob"] = "not-a-date"
        assert gql(CREATE, {"input": create_input})["errors"]

    def test_rejects_missing_required_field(self, gql, create_input):
        del create_input["country"]
        assert gql(CREATE, {"input": create_input})["errors"]


class TestUpdateAlert:
    def test_update_status_returns_fresh_stats(self, gql):
        payload = gql(UPDATE, {"id": 1, "input": {"status": "Closed"}})["data"]["updateAlert"]
        assert payload["alert"]["status"] == "Closed"
        assert payload["stats"]["open"] == 3
        assert payload["stats"]["closed"] == 3
        assert payload["stats"]["openHighSeverity"] == 2

    def test_partial_update_leaves_other_fields(self, gql):
        payload = gql(UPDATE, {"id": 1, "input": {"notes": "checked"}})["data"]["updateAlert"]
        assert payload["alert"] == {"id": 1, "status": "Open", "severity": "High", "notes": "checked"}

    def test_notes_can_be_cleared(self, gql):
        payload = gql(UPDATE, {"id": 1, "input": {"notes": None}})["data"]["updateAlert"]
        assert payload["alert"]["notes"] is None

    def test_null_status_rejected(self, gql):
        assert "cannot be null" in error_message(gql(UPDATE, {"id": 1, "input": {"status": None}}))

    def test_invalid_status_rejected(self, gql):
        assert "Invalid status" in error_message(gql(UPDATE, {"id": 1, "input": {"status": "Done"}}))

    def test_update_missing(self, gql):
        assert error_message(gql(UPDATE, {"id": 999, "input": {"notes": "x"}})) == "Alert 999 not found"


class TestDeleteAlert:
    def test_delete_returns_id_and_stats(self, gql):
        body = gql("mutation { deleteAlert(id: 1) { id stats { total open } } }")
        assert body["data"]["deleteAlert"] == {"id": 1, "stats": {"total": 7, "open": 3}}
        assert gql("{ alert(id: 1) { id } }")["errors"]

    def test_delete_missing(self, gql):
        assert error_message(gql("mutation { deleteAlert(id: 999) { id } }")) == "Alert 999 not found"
