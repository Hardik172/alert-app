"""Role-based access control shared by the GraphQL and REST APIs.

Roles are checked against permissions (never against role names directly),
so adding a role or moving a capability is a one-line change here.
"""
from typing import Optional

import db
from db import Role

ALERT_READ = "alert:read"
ALERT_CREATE = "alert:create"
ALERT_UPDATE = "alert:update"
ALERT_DELETE = "alert:delete"
USER_MANAGE = "user:manage"
AUDIT_READ = "audit:read"

ROLE_PERMISSIONS = {
    Role.VIEWER.value: [ALERT_READ],
    Role.ANALYST.value: [ALERT_READ, ALERT_CREATE, ALERT_UPDATE],
    # Read-only oversight: sees everything, changes nothing (separation of duties).
    Role.AUDITOR.value: [ALERT_READ, AUDIT_READ],
    Role.ADMIN.value: [ALERT_READ, ALERT_CREATE, ALERT_UPDATE, ALERT_DELETE, USER_MANAGE, AUDIT_READ],
}


class Unauthenticated(Exception):
    pass


class Forbidden(Exception):
    pass


def permissions_for(user: dict) -> list:
    return ROLE_PERMISSIONS.get(user["role"], [])


def token_from_header(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def user_from_header(authorization: Optional[str]) -> Optional[dict]:
    return db.user_for_token(token_from_header(authorization))


def can_view_history(user: dict, alert_id: int) -> bool:
    """Anyone who can read alerts may see a live alert's history. A deleted alert's history
    still holds its full original values, so it is only shown to users with audit:read."""
    return db.alert_exists(alert_id) or AUDIT_READ in permissions_for(user)


def check(user: Optional[dict], permission: str) -> dict:
    """Return the user if allowed, else raise Unauthenticated / Forbidden."""
    if not user:
        raise Unauthenticated("Not signed in")
    if permission not in permissions_for(user):
        raise Forbidden(f"Your role ({user['role']}) is not allowed to do this")
    return user
