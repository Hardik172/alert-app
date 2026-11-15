"""GraphQL schema (Strawberry). Field names are exposed in camelCase.

Mutations return the changed alert *and* fresh stats so the client can
update its view from a single response, with no follow-up requests.
"""
from datetime import date
from enum import Enum
from typing import List, Optional, Type

import strawberry
from graphql import GraphQLError
from strawberry.scalars import JSON
from strawberry.types import Info

import auth
import db


def _validate(value: Optional[str], enum: Type[Enum], field: str) -> Optional[str]:
    if value is None:
        return None
    allowed = [e.value for e in enum]
    if value not in allowed:
        raise GraphQLError(f"Invalid {field} '{value}'. Allowed: {', '.join(allowed)}")
    return value


def _not_blank(value: str, field: str) -> str:
    if not value or not value.strip():
        raise GraphQLError(f"{field} is required")
    return value.strip()


def _require(info: Info, permission: str) -> dict:
    """Enforce RBAC. Error `extensions.code` lets the client tell 'sign in again' from 'not allowed'."""
    try:
        return auth.check(info.context.get("user"), permission)
    except auth.Unauthenticated as e:
        raise GraphQLError(str(e), extensions={"code": "UNAUTHENTICATED"})
    except auth.Forbidden as e:
        raise GraphQLError(str(e), extensions={"code": "FORBIDDEN"})


# ---------- Types ----------

@strawberry.type
class User:
    id: int
    email: str
    name: str
    role: str
    created_at: str

    @strawberry.field
    def permissions(self) -> List[str]:
        return auth.permissions_for({"role": self.role})

    @classmethod
    def from_row(cls, row: dict) -> "User":
        return cls(**row)


@strawberry.type
class AuthPayload:
    token: str
    user: User


@strawberry.type
class Alert:
    id: int
    client_name: str
    dob: date
    address: str
    country: str
    alert_type: str
    severity: str
    raised_by: str
    status: str
    notes: Optional[str]
    raised_at: str

    @classmethod
    def from_row(cls, row: dict) -> "Alert":
        return cls(**{**row, "dob": date.fromisoformat(row["dob"])})


@strawberry.type
class TypeCount:
    alert_type: str
    count: int


@strawberry.type
class Stats:
    total: int
    open: int
    under_review: int
    closed: int
    open_high_severity: int
    by_type: List[TypeCount]

    @classmethod
    def load(cls) -> "Stats":
        s = db.get_stats()
        return cls(
            total=s["total"],
            open=s["by_status"].get("Open", 0),
            under_review=s["by_status"].get("Under Review", 0),
            closed=s["by_status"].get("Closed", 0),
            open_high_severity=s["open_high_severity"],
            by_type=[TypeCount(alert_type=k, count=v) for k, v in s["by_type"].items()],
        )


@strawberry.type
class Meta:
    alert_types: List[str]
    statuses: List[str]
    severities: List[str]
    roles: List[str]
    audit_actions: List[str]


@strawberry.type
class FieldChange:
    field: str
    from_: Optional[str] = strawberry.field(name="from")
    to: Optional[str]


def _str(v):
    return None if v is None else str(v)


@strawberry.type
class AuditEntry:
    id: int
    at: str
    actor_id: Optional[int]
    actor_name: str
    action: str
    entity_type: str
    entity_id: Optional[int]
    changes: List[FieldChange]
    _snapshot: strawberry.Private[Optional[dict]]

    @strawberry.field(description="Full record at deletion time. Only visible with audit:read.")
    def snapshot(self, info: Info) -> Optional[JSON]:
        user = info.context.get("user")
        if user and auth.AUDIT_READ in auth.permissions_for(user):
            return self._snapshot
        return None

    @classmethod
    def from_row(cls, row: Optional[dict]) -> Optional["AuditEntry"]:
        if row is None:
            return None
        return cls(
            id=row["id"], at=row["at"], actor_id=row["actor_id"], actor_name=row["actor_name"],
            action=row["action"], entity_type=row["entity_type"], entity_id=row["entity_id"],
            changes=[FieldChange(field=k, from_=_str(v[0]), to=_str(v[1])) for k, v in row["changes"].items()],
            _snapshot=row["snapshot"],
        )


@strawberry.type
class AuditPage:
    entries: List[AuditEntry]
    total: int


@strawberry.type
class AlertPayload:
    alert: Alert
    stats: Stats
    audit_entry: Optional[AuditEntry] = strawberry.field(
        description="The audit entry this change created (null if nothing changed).")


@strawberry.type
class DeletePayload:
    id: int
    stats: Stats
    audit_entry: AuditEntry


# ---------- Inputs ----------

@strawberry.input
class AlertFilter:
    search: Optional[str] = None
    alert_type: Optional[str] = None
    status: Optional[str] = None
    country: Optional[str] = None


@strawberry.input
class AuditFilter:
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    action: Optional[str] = None
    actor: Optional[str] = None
    since: Optional[date] = None
    until: Optional[date] = None


def _audit_kwargs(f: Optional[AuditFilter]) -> dict:
    f = f or AuditFilter()
    if f.action and f.action not in db.AUDIT_ACTIONS:
        raise GraphQLError(f"Invalid action '{f.action}'. Allowed: {', '.join(db.AUDIT_ACTIONS)}")
    return {
        "entity_type": f.entity_type or None, "entity_id": f.entity_id,
        "action": f.action or None, "actor": f.actor or None,
        "since": f.since.isoformat() if f.since else None,
        "until": f.until.isoformat() if f.until else None,
    }


@strawberry.input
class SignUpInput:
    email: str
    name: str
    password: str


@strawberry.input
class CreateAlertInput:
    client_name: str
    dob: date
    address: str
    country: str
    alert_type: str
    raised_by: str
    severity: Optional[str] = None
    notes: Optional[str] = None


@strawberry.input
class UpdateAlertInput:
    status: Optional[str] = strawberry.UNSET
    severity: Optional[str] = strawberry.UNSET
    notes: Optional[str] = strawberry.UNSET


# ---------- Resolvers ----------

def _get(alert_id: int) -> dict:
    try:
        return db.get_alert(alert_id)
    except db.NotFoundError as e:
        raise GraphQLError(str(e))


@strawberry.type
class Query:
    @strawberry.field
    def me(self, info: Info) -> Optional[User]:
        user = info.context.get("user")
        return User.from_row(user) if user else None

    @strawberry.field
    def users(self, info: Info) -> List[User]:
        _require(info, auth.USER_MANAGE)
        return [User.from_row(u) for u in db.list_users()]

    @strawberry.field
    def alerts(self, info: Info, filter: Optional[AlertFilter] = None) -> List[Alert]:
        _require(info, auth.ALERT_READ)
        f = filter or AlertFilter()
        rows = db.list_alerts(
            search=f.search or None,
            alert_type=_validate(f.alert_type or None, db.AlertType, "alertType"),
            status=_validate(f.status or None, db.AlertStatus, "status"),
            country=f.country or None,
        )
        return [Alert.from_row(r) for r in rows]

    @strawberry.field
    def alert(self, info: Info, id: int) -> Alert:
        _require(info, auth.ALERT_READ)
        return Alert.from_row(_get(id))

    @strawberry.field
    def stats(self, info: Info) -> Stats:
        _require(info, auth.ALERT_READ)
        return Stats.load()

    @strawberry.field
    def meta(self, info: Info) -> Meta:
        _require(info, auth.ALERT_READ)
        return Meta(**db.get_meta(), roles=[r.value for r in db.Role], audit_actions=db.AUDIT_ACTIONS)

    @strawberry.field(description="Change history of one alert, newest first. "
                                  "After deletion it is only available with audit:read.")
    def alert_history(self, info: Info, id: int) -> List[AuditEntry]:
        user = _require(info, auth.ALERT_READ)
        if not auth.can_view_history(user, id):
            raise GraphQLError(f"Alert {id} not found")
        rows, _ = db.list_audit(entity_type="alert", entity_id=id, limit=500)
        return [AuditEntry.from_row(r) for r in rows]

    @strawberry.field
    def audit_log(self, info: Info, filter: Optional[AuditFilter] = None,
                  limit: int = 50, offset: int = 0) -> AuditPage:
        _require(info, auth.AUDIT_READ)
        rows, total = db.list_audit(**_audit_kwargs(filter), limit=limit, offset=offset)
        return AuditPage(entries=[AuditEntry.from_row(r) for r in rows], total=total)


@strawberry.type
class Mutation:
    # ----- auth -----

    @strawberry.mutation
    def sign_up(self, input: SignUpInput) -> AuthPayload:
        """Self sign-up. Role is assigned by the server (first user Admin, then Viewer), never by the client."""
        try:
            user = db.create_user(input.email, input.name, input.password)
        except db.AuthError as e:
            raise GraphQLError(str(e))
        return AuthPayload(token=db.create_session(user["id"]), user=User.from_row(user))

    @strawberry.mutation
    def sign_in(self, email: str, password: str) -> AuthPayload:
        try:
            user = db.authenticate(email, password)
        except db.AuthError as e:
            raise GraphQLError(str(e))
        return AuthPayload(token=db.create_session(user["id"]), user=User.from_row(user))

    @strawberry.mutation
    def sign_out(self, info: Info) -> bool:
        token = info.context.get("token")
        if token:
            db.delete_session(token)
        return True

    @strawberry.mutation
    def set_user_role(self, info: Info, id: int, role: str) -> User:
        actor = _require(info, auth.USER_MANAGE)
        try:
            return User.from_row(db.set_user_role(id, role, actor))
        except (db.AuthError, db.NotFoundError) as e:
            raise GraphQLError(str(e))

    # ----- alerts -----

    @strawberry.mutation
    def create_alert(self, info: Info, input: CreateAlertInput) -> AlertPayload:
        actor = _require(info, auth.ALERT_CREATE)
        row, audit_id = db.create_alert({
            "client_name": _not_blank(input.client_name, "clientName"),
            "dob": input.dob.isoformat(),
            "address": _not_blank(input.address, "address"),
            "country": _not_blank(input.country, "country"),
            "alert_type": _validate(input.alert_type, db.AlertType, "alertType"),
            "severity": _validate(input.severity, db.Severity, "severity"),
            "raised_by": _not_blank(input.raised_by, "raisedBy"),
            "notes": input.notes,
        }, actor)
        return AlertPayload(alert=Alert.from_row(row), stats=Stats.load(),
                            audit_entry=AuditEntry.from_row(db.get_audit(audit_id)))

    @strawberry.mutation
    def update_alert(self, info: Info, id: int, input: UpdateAlertInput) -> AlertPayload:
        actor = _require(info, auth.ALERT_UPDATE)
        changes = {}
        if input.status is not strawberry.UNSET:
            changes["status"] = _validate(input.status, db.AlertStatus, "status")
        if input.severity is not strawberry.UNSET:
            changes["severity"] = _validate(input.severity, db.Severity, "severity")
        if input.notes is not strawberry.UNSET:
            changes["notes"] = input.notes
        if changes.get("status", "") is None or changes.get("severity", "") is None:
            raise GraphQLError("status and severity cannot be null")
        try:
            row, audit_id = db.update_alert(id, changes, actor)
        except db.NotFoundError as e:
            raise GraphQLError(str(e))
        return AlertPayload(alert=Alert.from_row(row), stats=Stats.load(),
                            audit_entry=AuditEntry.from_row(db.get_audit(audit_id)))

    @strawberry.mutation
    def delete_alert(self, info: Info, id: int) -> DeletePayload:
        actor = _require(info, auth.ALERT_DELETE)
        try:
            audit_id = db.delete_alert(id, actor)
        except db.NotFoundError as e:
            raise GraphQLError(str(e))
        return DeletePayload(id=id, stats=Stats.load(), audit_entry=AuditEntry.from_row(db.get_audit(audit_id)))


schema = strawberry.Schema(query=Query, mutation=Mutation)
