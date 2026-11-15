"""Data-access layer tests (db.py)."""
import pytest

import db

SEED_COUNT = len(db.SEED)


class TestSeeding:
    def test_seeds_sample_data_on_init(self):
        assert len(db.list_alerts()) == SEED_COUNT

    def test_init_is_idempotent(self):
        db.init_db()
        db.init_db()
        assert len(db.list_alerts()) == SEED_COUNT

    def test_self_heals_when_db_file_deleted(self, temp_db):
        temp_db.unlink()
        assert len(db.list_alerts()) == SEED_COUNT

    def test_self_heals_when_db_file_is_empty(self, temp_db):
        temp_db.unlink()
        temp_db.touch()  # 0-byte file, the state that caused "no such table"
        assert len(db.list_alerts()) == SEED_COUNT


class TestListAlerts:
    def test_ordered_newest_first(self):
        ids = [a["id"] for a in db.list_alerts()]
        assert ids == sorted(ids, reverse=True)

    def test_filter_by_type(self):
        rows = db.list_alerts(alert_type="Fraud")
        assert {r["client_name"] for r in rows} == {"Chen Wei", "Carlos Silva"}

    def test_filter_by_status(self):
        rows = db.list_alerts(status="Under Review")
        assert {r["client_name"] for r in rows} == {"Maria Garcia", "Chen Wei"}

    def test_filter_by_country(self):
        rows = db.list_alerts(country="India")
        assert [r["client_name"] for r in rows] == ["Aisha Khan"]

    def test_search_is_case_insensitive_and_covers_name_address_raised_by(self):
        # "chen" matches client "Chen Wei" and raiser "David Chen" (2 alerts)
        names = {r["client_name"] for r in db.list_alerts(search="CHEN")}
        assert names == {"Chen Wei", "Maria Garcia", "Fatima Al-Sayed"}
        assert [r["client_name"] for r in db.list_alerts(search="baker street")] == ["John Smith"]

    def test_combined_filters(self):
        rows = db.list_alerts(alert_type="AML", status="Open", search="dubai")
        assert [r["client_name"] for r in rows] == ["Fatima Al-Sayed"]

    def test_no_match_returns_empty(self):
        assert db.list_alerts(search="zzz-no-match") == []

    def test_search_is_parameterised(self):
        # SQL injection attempt is treated as a literal search string
        assert db.list_alerts(search="' OR 1=1 --") == []
        assert len(db.list_alerts()) == SEED_COUNT


class TestCreate:
    def test_create_sets_open_status_and_timestamp(self, new_alert):
        row, _ = db.create_alert(new_alert)
        assert row["id"] > SEED_COUNT
        assert row["status"] == "Open"
        assert row["raised_at"]
        assert row["client_name"] == "Test Client"
        assert db.get_alert(row["id"]) == row

    def test_create_defaults_severity_to_medium(self, new_alert):
        new_alert.pop("severity")
        assert db.create_alert(new_alert)[0]["severity"] == "Medium"


class TestUpdate:
    def test_partial_update(self):
        row, _ = db.update_alert(1, {"notes": "reviewed"})
        assert row["notes"] == "reviewed"
        assert row["status"] == "Open"  # unchanged

    def test_ignores_non_updatable_fields(self):
        before = db.get_alert(1)
        row, _ = db.update_alert(1, {"client_name": "Hacked", "dob": "2000-01-01", "status": "Closed"})
        assert row["client_name"] == before["client_name"]
        assert row["dob"] == before["dob"]
        assert row["status"] == "Closed"

    def test_empty_update_is_noop(self):
        assert db.update_alert(1, {}) == (db.get_alert(1), None)

    def test_update_missing_raises(self):
        with pytest.raises(db.NotFoundError):
            db.update_alert(999, {"notes": "x"})


class TestDeleteAndGet:
    def test_get_missing_raises(self):
        with pytest.raises(db.NotFoundError):
            db.get_alert(999)

    def test_delete(self):
        db.delete_alert(1)
        with pytest.raises(db.NotFoundError):
            db.get_alert(1)
        assert len(db.list_alerts()) == SEED_COUNT - 1

    def test_delete_missing_raises(self):
        with pytest.raises(db.NotFoundError):
            db.delete_alert(999)


class TestStats:
    def test_seed_stats(self):
        s = db.get_stats()
        assert s["total"] == SEED_COUNT
        assert s["by_status"] == {"Open": 4, "Under Review": 2, "Closed": 2}
        assert s["by_type"]["AML"] == 2
        assert s["open_high_severity"] == 3

    def test_closing_high_severity_alert_reduces_active_high_count(self):
        db.update_alert(1, {"status": "Closed"})  # John Smith, High
        s = db.get_stats()
        assert s["open_high_severity"] == 2
        assert s["by_status"]["Closed"] == 3

    def test_meta_lists_all_enum_values(self):
        m = db.get_meta()
        assert m["statuses"] == ["Open", "Under Review", "Closed"]
        assert "KYC Incomplete" in m["alert_types"]
        assert m["severities"] == ["Low", "Medium", "High"]
