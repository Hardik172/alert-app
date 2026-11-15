---
name: add-permission
description: Change role-based access control in Client Alerts — add a permission, add a role, move a capability between roles, or protect a new operation. Use when asked who can do what, to restrict or allow an action for a role, or to add a new role.
---

# Change roles and permissions

All access rules come from `ROLE_PERMISSIONS` in `backend/auth.py`. Code checks permission strings like `"alert:delete"`, never role names. Keep it that way.

## Protect a new operation

1. In `backend/auth.py`, add a constant (`REPORT_EXPORT = "report:export"`) and add it to the roles that should have it in `ROLE_PERMISSIONS`.
2. In GraphQL (`backend/schema.py`), the resolver must call `_require(info, auth.REPORT_EXPORT)` as its first line, before any DB access.
3. In REST (`backend/main.py`), add `dependencies=[Depends(require(auth.REPORT_EXPORT))]` to the route decorator.
4. In the frontend:
   - Add the permission to the `PERM` map in `App.jsx` and gate the UI with `can(PERM.x)`. Hide buttons the user can't use, and disable fields they can see but not edit.
   - Mirror it in `src/test/mockServer.js`: add it to `PERMISSIONS` and add the operation to `REQUIRES`.

## Move a capability between roles

Change only `ROLE_PERMISSIONS` and the mirrored `PERMISSIONS` in `mockServer.js`. The UI updates automatically because it reads `user.permissions` from the server.

## Add a role

1. Add it to the `Role` enum in `backend/db.py` and to `ROLE_PERMISSIONS`.
2. The frontend gets roles from `meta.roles`, so the Users admin dropdown picks it up automatically. Add a `.role-<name>` badge style in `styles.css` using existing color tokens, a `ROLE_HELP` entry in `UserAdmin.jsx`, and an entry in `META.roles` in the mock.
3. Decide with the user whether new sign-ups should get this role. Don't change the default (first user Admin, then Viewer) without being asked.

## Invariants to preserve

- The client never chooses its own role. `signUp` has no role argument.
- The last Admin can't be demoted (`db.set_user_role`).
- Permissions are read from the DB on every request, so role changes apply to existing sessions. Don't cache roles in the token.
- Unauthenticated requests return code `UNAUTHENTICATED`. Signed-in requests without permission return `FORBIDDEN`.

## Tests

- **Backend:** add the operation to `MATRIX` in `tests/test_auth.py`, mapped to the set of roles allowed. The parametrized tests then check every role, plus anonymous access, automatically. Add REST cases to `TestREST.test_permissions`.
- **Frontend:** add a case to the "role-based access" block in `src/Auth.test.jsx`, rendering with `startSignedIn(role)`.
- Run the `run-checks` skill, then update the roles table in `README.md`.
