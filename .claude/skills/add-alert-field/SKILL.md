---
name: add-alert-field
description: Add a new field to Client Alerts end to end (SQLite column, GraphQL type and inputs, REST models, frontend query fragment, mock server, table/detail/form UI, tests). Use when asked to add, track, show or store a new piece of information on an alert.
---

# Add a field to alerts

An alert field runs through every layer. Missing one leaves the field null in the UI or makes tests fail in confusing ways. Work through the layers in this order.

Before you start, decide:
- the name (snake_case in Python, camelCase in GraphQL and JS)
- the type, and whether it is required
- whether it is set at creation, editable later, or both
- if it has a fixed set of values: an `Enum` in `db.py`, exposed through `meta`

## Backend

1. **`backend/db.py`**
   - Add the column to `CREATE TABLE alerts` in `_create_schema`.
   - Existing databases (including the user's `alerts.db`) won't get the new column from `CREATE TABLE IF NOT EXISTS`. In `_create_schema`, add an `ALTER TABLE alerts ADD COLUMN ...` guarded by `PRAGMA table_info(alerts)`. Never delete or recreate the user's DB.
   - Add the value to every `SEED` tuple and to `INSERT_SQL` / `create_alert`.
   - If it is editable, add it to `UPDATABLE_FIELDS`. Updates are audited automatically, because `update_alert` compares the row before and after.
   - Add it to `AUDITED_ALERT_FIELDS`, so its initial value is recorded in the `alert.create` audit entry.
   - In `frontend/src/auditFormat.js`, add a readable label to `FIELD_LABELS` if the snake_case name doesn't read well, and add it to `LONG` if it is free text.
   - If it has fixed values, add an `Enum` and include it in `get_meta()`.
2. **`backend/schema.py`**
   - Add the field to the `Alert` type, `CreateAlertInput`, and `UpdateAlertInput` if editable. Fields on `UpdateAlertInput` default to `strawberry.UNSET`.
   - Validate it in the resolver with `_validate` (enum) or `_not_blank` (required text).
   - If it has fixed values, add it to `Meta`.
   - Don't add a new permission check. The existing `_require` on each resolver already covers the field.
3. **`backend/main.py`**: add the field to `AlertBase` (or `AlertUpdate`) with matching validation.
4. **Tests**: update `tests/conftest.py::new_alert`, the GraphQL `create_input` fixture, and any assertion that lists all fields. Add tests for validation and for create/update round-trips.

## Frontend

5. **`frontend/src/api.js`**: add the field to the `AlertFields` fragment. If it is filterable, also add it to `matchesFilter` so in-place updates stay correct.
6. **`frontend/src/test/mockServer.js`**: add it to every `SEED` row (and to `META` if it has fixed values).
7. **`frontend/src/App.jsx`**
   - Add it to `EMPTY_FORM` and `NewAlertForm` if it is set at creation.
   - Add it to the `DetailPanel` `<dl>`. If it is editable, add a control that is disabled when `!canUpdate`.
   - Add a table column only if it is short and useful for scanning, and update `colSpan` on the empty and loading rows.
   - Use existing CSS tokens for any styling.
8. **Tests**: extend the "shows every required client detail" and create-form assertions in `App.test.jsx`.

## Finish

- Run the `run-checks` skill.
- Update `README.md` (the field list in the intro and any example query).
- Tell the user that their running server picks up the column migration on its next reload.
