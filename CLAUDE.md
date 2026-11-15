# Client Alerts app

A compliance tool for viewing and managing flagged clients. The backend is FastAPI with GraphQL (Strawberry) and REST, on SQLite. The frontend is React 18 on Vite, with no UI or state libraries. `README.md` covers the API in full. This file covers how to work on the code.

## Commands

```bash
# Backend (from backend/)
source .venv/bin/activate
pytest -q                                   # ~1s, uses a temp DB per test
uvicorn main:app --reload --port 8000       # the user's dev server; don't start it on 8000 yourself

# Frontend (from frontend/)
npm test                                    # Vitest single run
npm run build -- --outDir <scratch>/dist    # build check; never write into frontend/dist unasked
```

Run both suites after any change that crosses the API boundary.

## Layout

| File | Role |
|---|---|
| `backend/db.py` | All SQL. Enums (`AlertType`, `AlertStatus`, `Severity`, `Role`), users, sessions, password hashing, the append-only `audit_log` |
| `backend/auth.py` | `ROLE_PERMISSIONS` map and `check()`. This is the only place roles turn into permissions |
| `backend/schema.py` | GraphQL types, resolvers, `_require(info, perm)` guard |
| `backend/main.py` | FastAPI app, GraphQL context (`graphql_context`), REST routes with `Depends(require(perm))` |
| `frontend/src/api.js` | GraphQL client, token storage (`session`), `ApiError` with `.code`, all operations |
| `frontend/src/App.jsx` | Auth gate (`App`) and `Dashboard`, `DetailPanel`, `NewAlertForm` |
| `frontend/src/AuthPage.jsx`, `UserAdmin.jsx` | Sign in / sign up page, admin role management |
| `frontend/src/AuditLog.jsx`, `auditFormat.js` | Audit log screen; readable text for audit entries (also used by the History timeline) |
| `frontend/src/test/mockServer.js` | In-memory fake GraphQL server used by every frontend test |

## Conventions that matter

- **One request per user action.** Page load is a single `InitialLoad` (`me` + alerts + stats + meta). Mutations return the changed object and fresh `stats`, so the UI patches its state in place and never refetches. Tests check this with `server.ops()`, so keep it true.
- **RBAC is permission-based.** Code checks permissions like `alert:delete`, never role names. To change who can do what, edit `ROLE_PERMISSIONS` in `auth.py` (the mock server mirrors it in `PERMISSIONS`). Every new GraphQL resolver calls `_require(info, ...)` and every new REST route gets `dependencies=[Depends(require(...))]`. The server enforces access; the UI only hides what the user can't use.
- **Every write is audited, in the same transaction.** Any function in `db.py` that changes data takes `actor` and calls `_audit(db, actor, ...)` inside the **same** `with get_db() as db:` block, before it returns. `create_alert` and `update_alert` return `(row, audit_id)`, `delete_alert` returns `audit_id`, and the GraphQL mutations return that entry as `auditEntry`. Resolvers and REST routes get the actor from `_require(...)` / `Depends(require(...))`. If you add a new action string, add it to `db.AUDIT_ACTIONS`, `ACTION_LABELS` in `auditFormat.js`, and the mock's `META.auditActions`. Never write code that updates or deletes `audit_log` rows; triggers reject it.
- **Check-then-write needs the write lock.** If a function reads something and then writes based on it (last-admin guard, first-user-is-Admin, unique email), open it with `get_db(immediate=True)`. That runs `BEGIN IMMEDIATE`, so concurrent requests queue instead of both passing the check. Do slow work, such as password hashing, *before* taking the lock. `tests/test_review_fixes.py::TestRaces` shows how to test this: slow down the read with `monkeypatch` and run two threads.
- **Auth errors** carry `extensions.code`: `UNAUTHENTICATED` means sign in again, `FORBIDDEN` means the role isn't allowed. The frontend routes `UNAUTHENTICATED` through `handleAuthError` and back to the sign-in page.
- **Roles are assigned by the server.** The first user becomes Admin, later users become Viewer, and the last Admin can't be demoted. Never let the client choose a role at sign-up.
- **Display strings are the API values** (`"Under Review"`, `"KYC Incomplete"`). The server validates them against the enums in `db.py`.
- GraphQL fields are camelCase and Python is snake_case. Strawberry converts automatically.
- Use the existing CSS custom properties in `styles.css` for colors. Each one is defined for both light and `[data-theme="dark"]`. Don't hard-code colors.

## Testing rules

- Backend fixtures (`tests/conftest.py`): `client` and `gql` act as an Admin, `anon_client` and `anon_gql` have no session, `token_for(role)` creates a user and returns a token, and `gql(query, vars, token=...)` acts as anyone. PBKDF2 iterations are lowered in tests to keep them fast.
- In frontend tests, `renderApp(opts, role)` stores `tokenFor(role)` before rendering. Seeded users are `admin@`, `analyst@`, `auditor@` and `viewer@test.io`, all with password `password123`. `installMockServer({ audit: [...] })` seeds audit entries (camelCase, newest first), and `server.audit()` returns the current audit store.
- When an API operation changes, update `mockServer.js` (handler, plus `REQUIRES` if it needs a permission) together with `api.js`.
- Never touch `backend/alerts.db` or the servers on `:8000`/`:5173`. For a live end-to-end check, start uvicorn on another port with `ALERTS_DB_PATH=<scratch>/e2e.db`.
- Keep the test counts and the test table in `README.md` current.

## Skills

Project skills live in `.claude/skills/`:
- `run-checks`: full test, build and optional live smoke check
- `add-alert-field`: add a new field to alerts end to end
- `add-permission`: add a new permission or role, or change what a role can do
