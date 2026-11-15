"""REST API tests (/api routes in main.py)."""


def test_list_alerts(client):
    res = client.get("/api/alerts")
    assert res.status_code == 200
    assert len(res.json()) == 8


def test_list_with_filters(client):
    res = client.get("/api/alerts", params={"alert_type": "Fraud", "status": "Closed"})
    assert [a["client_name"] for a in res.json()] == ["Carlos Silva"]


def test_list_invalid_enum_is_422(client):
    assert client.get("/api/alerts", params={"status": "Nope"}).status_code == 422


def test_get_alert(client):
    res = client.get("/api/alerts/1")
    assert res.status_code == 200
    assert res.json()["client_name"] == "John Smith"


def test_get_missing_is_404(client):
    res = client.get("/api/alerts/999")
    assert res.status_code == 404
    assert res.json()["detail"] == "Alert not found"


def test_create(client, new_alert):
    res = client.post("/api/alerts", json=new_alert)
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "Open"
    assert body["dob"] == "1990-01-15"
    assert client.get(f"/api/alerts/{body['id']}").status_code == 200


def test_create_validation(client, new_alert):
    new_alert["alert_type"] = "Spam"
    assert client.post("/api/alerts", json=new_alert).status_code == 422
    new_alert["alert_type"] = "PEP"
    new_alert["client_name"] = ""
    assert client.post("/api/alerts", json=new_alert).status_code == 422


def test_patch(client):
    res = client.patch("/api/alerts/1", json={"status": "Under Review", "notes": "escalated"})
    assert res.status_code == 200
    assert res.json()["status"] == "Under Review"
    assert res.json()["notes"] == "escalated"
    assert res.json()["severity"] == "High"


def test_patch_missing_is_404(client):
    assert client.patch("/api/alerts/999", json={"notes": "x"}).status_code == 404


def test_delete(client):
    assert client.delete("/api/alerts/1").status_code == 204
    assert client.get("/api/alerts/1").status_code == 404
    assert client.delete("/api/alerts/1").status_code == 404


def test_stats_and_meta(client):
    stats = client.get("/api/alerts/stats").json()
    assert stats["total"] == 8
    assert stats["by_status"]["Open"] == 4
    meta = client.get("/api/meta").json()
    assert meta["severities"] == ["Low", "Medium", "High"]
