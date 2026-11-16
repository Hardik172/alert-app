# Client Alerts App

FastAPI backend (GraphQL + REST) and React (Vite) frontend for viewing and managing flagged clients.

Each alert shows: client name, date of birth, address, country, alert type, severity, raised by, status, and notes.

Users must **sign in** (or **sign up**) to use the app, and what they can do depends on their role.

## Accounts and roles

| Role | Can |
|---|---|
| **Pending** | Nothing. New sign-ups wait here until an Admin assigns a role |
| **Viewer** | View alerts, stats and details (read-only) |
| **Analyst** | Everything a Viewer can, plus flag clients (create alerts) and change status / severity / notes |
| **Auditor** | Everything a Viewer can, plus the full **Audit log** and CSV export. Can't change anything (separation of duties) |
| **Admin** | Everything, plus delete alerts, change other users' roles (**Users** button) and the **Audit log** |

- The **first account created becomes Admin**. Everyone who signs up after that starts as **Pending** with no access, and sees a "Waiting for access" screen until an admin gives them a role. Users can never pick their own role.
- The last remaining Admin can't be demoted, so you can't lock yourself out.
- Role changes take effect immediately, even for users who are already signed in.
- Sign-up page: http://localhost:5173/#/signup

## Audit trail

Every change is recorded with **who**, **when** and **what changed**:

| Recorded | Details kept |
|---|---|
| Alert created / updated / deleted | Each changed field as old → new. Deletes keep a full snapshot of the record |
| Role changed | Old → new role, and the affected user's name and email |
| Sign up, sign in, failed sign-in, sign out | For failed sign-ins, the email that was tried (never the password) |

- **Atomic:** each audit row is written in the same database transaction as the change, so there's never a change without its record (or the reverse). Saving a value that didn't change records nothing.
- **Append-only:** SQLite triggers reject any `UPDATE` or `DELETE` on `audit_log`, so entries can't be edited or removed through the app or by hand.
- **Per-alert history:** expand **History** in an alert's detail panel. Anyone who can view the alert can see who changed it. Once an alert is deleted, its history (which still contains the original values) is only available to Auditors and Admins; everyone else gets "not found".
- **Audit log screen** (Auditor, Admin): filter by action, user and date range, page through results, and **Export CSV**. In the CSV, cells that start with `=`, `+`, `-` or `@` get a leading `'` so spreadsheets can't run them as formulas.
- History starts from when auditing was added. Earlier edits and the seeded sample alerts have no entries.

Security notes: passwords are hashed with PBKDF2-SHA256 (240k iterations, per-user salt). Sessions are random bearer tokens valid for 7 days; only a SHA-256 hash of each token is stored, and signing out revokes it server-side. The token is kept in the browser's `localStorage`. Permissions are enforced by the server on every GraphQL field and REST route (the UI only hides what you can't use). Sign-in takes the same time whether or not the email has an account, so response times don't reveal who is registered. Email, name and password lengths are capped (254 / 100 / 1024 characters). Role changes and sign-ups take the database write lock first, so two simultaneous requests can't both demote the last two admins or both become the first Admin. There is no login rate limiting or password reset yet.

## Run the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

- GraphQL explorer (GraphiQL): http://127.0.0.1:8000/graphql
- REST docs: http://127.0.0.1:8000/docs

Data (alerts, users, sessions and the audit log) is stored in `backend/alerts.db` (SQLite) and seeded with sample alerts on first start. Delete that file to reset, which also removes all accounts and the audit history.

## Run the frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/graphql` and `/api` to the backend on port 8000.

## Run the tests

```bash
# Backend: pytest (226 tests)
cd backend
pip install -r requirements-dev.txt
pytest

# Frontend: Vitest + React Testing Library (65 tests)
cd frontend
npm test            # single run
npm run test:watch  # re-run on save
```

Backend tests use a temporary SQLite file per test, so they never touch `alerts.db` or a running server. Frontend tests replace `fetch` with an in-memory fake GraphQL server (`src/test/mockServer.js`, with seeded Admin/Analyst/Auditor/Viewer users), so no backend is needed.

| Suite | Covers |
|---|---|
| `backend/tests/test_db.py` | Seeding, self-healing DB, filters/search, create/update/delete, stats, SQL-injection safety |
| `backend/tests/test_graphql.py` | Queries, filters, mutations returning fresh stats, input validation, not-found errors |
| `backend/tests/test_rest.py` | REST endpoints, status codes (201/204/404/422) |
| `backend/tests/test_auth.py` | Password hashing, sessions/expiry, sign up/in/out, full role × operation permission matrix (GraphQL + REST), user role management, last-admin guard |
| `backend/tests/test_review_fixes.py` | Regression tests from the pre-push review: concurrent last-admin / first-user / duplicate-email races, deleted-alert history access, REST null and blank-field validation, sign-in timing and input length limits, GraphQL WebSockets refused |
| `backend/tests/test_audit.py` | Every write is audited with the right actor and diff, no-op updates aren't, the change and its audit row roll back together, append-only triggers, failed sign-ins never store passwords, snapshot visibility, filters/pagination, CSV export and formula escaping |
| `frontend/src/api.test.js` | GraphQL client: request shape, empty-filter stripping, error handling, `matchesFilter` |
| `frontend/src/Auth.test.jsx` | Sign in / sign up / sign out, validation, expired sessions, what each role sees, admin role changes |
| `frontend/src/Audit.test.jsx` | History loads on demand and updates in place, Audit log visibility per role, filters, pagination, deleted-record snapshot, CSV export with the session token |
| `frontend/src/App.test.jsx` | Initial load is 1 request; debounced filtering; detail panel; in-place updates with no refetch; delete confirmation; create form; error states; dark mode |

## Project layout

| File | Purpose |
|---|---|
| `backend/db.py` | SQLite data access (alerts, users, sessions), shared by both APIs |
| `backend/auth.py` | Roles → permissions map and access checks |
| `backend/schema.py` | GraphQL schema (Strawberry) |
| `backend/main.py` | FastAPI app: mounts `/graphql` and the REST routes |
| `frontend/src/api.js` | Small GraphQL client and all queries/mutations |
| `frontend/src/App.jsx` | Auth gate + dashboard UI |
| `frontend/src/AuthPage.jsx` | Sign in / sign up page |
| `frontend/src/UserAdmin.jsx` | Admin: users & roles |
| `frontend/src/AuditLog.jsx` | Audit log screen (filters, pages, CSV export) |
| `frontend/src/auditFormat.js` | Turns audit entries into readable text (shared by History and Audit log) |

## GraphQL API (used by the frontend)

The frontend makes one request per user action:

| Action | Request | Returns |
|---|---|---|
| Sign in / sign up | `signIn` / `signUp` | session token + user (role, permissions) |
| Page load | `query InitialLoad` | current user + alerts + stats + dropdown options |
| Change a filter (debounced 250 ms) | `query Alerts` | alerts only |
| Create / update an alert | `createAlert` / `updateAlert` | the changed alert + fresh stats + its `auditEntry` |
| Delete an alert | `deleteAlert` | deleted id + fresh stats + its `auditEntry` |
| Expand an alert's History (first time) | `query AlertHistory` | that alert's audit entries |
| Open / filter / page the Audit log | `query AuditLog` | one page of entries + total |

Mutations return everything the screen needs, so the app updates rows in place and never refetches the list or reloads the page.

Example:

```graphql
query {
  alerts(filter: { alertType: "Fraud", status: "Open", search: "chen" }) {
    id clientName dob address country alertType severity raisedBy status
  }
  stats { total open underReview closed openHighSeverity }
}

mutation {
  updateAlert(id: 3, input: { status: "Under Review", notes: "Escalated" }) {
    alert { id status notes }
    stats { open underReview }
  }
}
```

Every request except `signIn` / `signUp` must send `Authorization: Bearer <token>`. Auth failures come back as GraphQL errors with `extensions.code` = `UNAUTHENTICATED` (sign in again) or `FORBIDDEN` (role not allowed). In GraphiQL, add the header under "Headers".

Alert types, statuses and severities are passed as their display strings (e.g. `"Under Review"`) and validated by the server. The allowed values come from `meta { alertTypes statuses severities }`.

## REST API (kept for other consumers)

All alert and user routes require `Authorization: Bearer <token>` (401 if missing/invalid, 403 if your role lacks permission).

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/signup` | Create an account → `{token, user}` |
| POST | `/api/auth/signin` | Sign in → `{token, user}` |
| POST | `/api/auth/signout` | Revoke the current token |
| GET | `/api/auth/me` | Current user, role and permissions |
| GET | `/api/users` | List users (Admin) |
| PATCH | `/api/users/{id}` | Change a user's role, `{"role": "Analyst"}` (Admin) |
| GET | `/api/alerts?search=&alert_type=&status=&country=` | List and filter alerts |
| GET | `/api/alerts/{id}` | Get one alert |
| POST | `/api/alerts` | Flag a client (create an alert) |
| PATCH | `/api/alerts/{id}` | Update status, severity, or notes |
| DELETE | `/api/alerts/{id}` | Delete an alert |
| GET | `/api/alerts/stats` | Counts by status/type |
| GET | `/api/meta` | Allowed alert types, statuses, severities |
| GET | `/api/alerts/{id}/history` | One alert's audit entries, newest first (works after deletion) |
| GET | `/api/audit?entity_type=&entity_id=&action=&actor=&since=&until=&limit=&offset=` | Audit log page `{entries, total}` (Auditor, Admin) |
| GET | `/api/audit.csv?…same filters…` | Whole filtered audit log as a CSV download (Auditor, Admin) |
