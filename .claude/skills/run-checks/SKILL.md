---
name: run-checks
description: Run the Client Alerts app's full verification (backend pytest, frontend Vitest, production build, optional live smoke test against a scratch server) without touching the user's dev servers or alerts.db. Use after changing alert-app code, or when asked to test, verify or check the app.
---

# Run checks for alert-app

Run each step and report the real result. If something fails, show the failing output and stop. Don't say it passed.

## 1. Backend tests

```bash
cd alert-app/backend && source .venv/bin/activate && python -m pytest -q
```

Each test gets its own temp SQLite file (the `temp_db` autouse fixture), so this is safe to run while the user's server is up.

## 2. Frontend tests

```bash
cd alert-app/frontend && npx vitest run
```

These use the fake server in `src/test/mockServer.js`. If a test fails with "Unable to find … John Smith", the app probably landed on the sign-in page. Check that the test stores a token (`renderApp` does this) and that the mock handler exists for any new operation.

## 3. Production build

Build into the scratchpad, never into `frontend/dist`:

```bash
cd alert-app/frontend && npx vite build --outDir "$SCRATCH/dist" --emptyOutDir && rm -rf "$SCRATCH/dist"
```

## 4. Live smoke test (only for auth or API changes)

Never use ports 8000 or 5173, because those are the user's servers. Use a scratch port and a scratch DB:

```bash
cd alert-app/backend && source .venv/bin/activate
PORT=8765
lsof -tiTCP:$PORT -sTCP:LISTEN && echo "port busy, pick another" && exit 1
rm -f "$SCRATCH/e2e.db"
ALERTS_DB_PATH="$SCRATCH/e2e.db" uvicorn main:app --port $PORT > "$SCRATCH/uv.log" 2>&1 & UVPID=$!
```

**Check that the port is free first.** A leftover smoke-test server from an earlier session will keep answering with *old* code while the new one silently fails to bind. The symptom is errors like "Cannot query field …" for fields you just added.

Then:
- Sign up two users through `signUp`. The first should come back as `Admin`, the second as `Viewer`.
- `{ alerts { id } }` with no header should return `extensions.code = "UNAUTHENTICATED"`.
- `deleteAlert` with the Viewer token should return `FORBIDDEN`. With the Admin token it should succeed.
- REST `DELETE /api/alerts/2` should return 401 (no token), 403 (Viewer) and 204 (Admin).

Pass headers as separate quoted `-H "Authorization: Bearer $TOKEN"` arguments. Building them through `${var:+...}` expansion breaks the quoting and looks like an auth bug.

Audit checks:
- Mutations return an `auditEntry`.
- `alertHistory(id)` lists create, then update, then delete, newest first.
- An Auditor can read `auditLog` and `/api/audit.csv`, and an Analyst gets FORBIDDEN / 403.
- With rows present, `DELETE FROM audit_log` fails with "audit log is append-only". Row-level triggers don't fire on an empty table, so an empty table proves nothing.

Afterwards, stop the server **by its PID** (`kill $UVPID`). Don't use `kill %1`, which may not reach a server started inside a subshell. Confirm the port is free with `lsof`, then delete `$SCRATCH/e2e.db`.

## 5. Report

Give pass/fail counts per suite, whether the build passed, and the smoke-test results. If the counts changed, update the test counts in `alert-app/README.md`.
