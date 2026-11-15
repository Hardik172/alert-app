"""Client Alerts API - FastAPI app exposing GraphQL (/graphql) and REST (/api)."""
import csv
import io
import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator
from typing_extensions import Annotated
from strawberry.fastapi import GraphQLRouter

import auth
import db
from db import AlertStatus, AlertType, Role, Severity
from schema import schema


# Trimmed and non-blank, matching GraphQL's _not_blank checks.
RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class AlertBase(BaseModel):
    client_name: RequiredText
    dob: date
    address: RequiredText
    country: RequiredText
    alert_type: AlertType
    severity: Severity = Severity.MEDIUM
    raised_by: RequiredText
    notes: Optional[str] = None


class AlertCreate(AlertBase):
    pass


class AlertUpdate(BaseModel):
    """Omit a field to leave it unchanged. `notes` may be null (cleared); status/severity may not."""
    status: Optional[AlertStatus] = None
    severity: Optional[Severity] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _no_null_status_or_severity(self):
        for field in ("status", "severity"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class Alert(AlertBase):
    id: int
    status: AlertStatus
    raised_at: datetime


class SignUp(BaseModel):
    email: str
    name: str
    password: str


class SignIn(BaseModel):
    email: str
    password: str


class User(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    created_at: datetime
    permissions: List[str]


class AuthResponse(BaseModel):
    token: str
    user: User


class RoleUpdate(BaseModel):
    role: Role


class AuditEntry(BaseModel):
    id: int
    at: datetime
    actor_id: Optional[int]
    actor_name: str
    action: str
    entity_type: str
    entity_id: Optional[int]
    changes: Dict[str, List[Any]]
    snapshot: Optional[Dict[str, Any]] = None


class AuditPage(BaseModel):
    entries: List[AuditEntry]
    total: int


def _user_out(user: dict) -> dict:
    return {**user, "permissions": auth.permissions_for(user)}


# ---------- Auth dependencies ----------

def current_user(authorization: Optional[str] = Header(None)) -> dict:
    user = auth.user_from_header(authorization)
    if not user:
        raise HTTPException(401, "Not signed in", headers={"WWW-Authenticate": "Bearer"})
    return user


def require(permission: str):
    def dependency(user: dict = Depends(current_user)) -> dict:
        try:
            return auth.check(user, permission)
        except auth.Forbidden as e:
            raise HTTPException(403, str(e))
    return dependency


def graphql_context(request: Request) -> dict:
    token = auth.token_from_header(request.headers.get("authorization"))
    return {"token": token, "user": db.user_for_token(token)}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Client Alerts API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# GraphQL endpoint (GraphiQL explorer available at GET /graphql in a browser)
app.include_router(GraphQLRouter(schema, context_getter=graphql_context), prefix="/graphql")


def _or_404(fn, *args):
    try:
        return fn(*args)
    except db.NotFoundError:
        raise HTTPException(status_code=404, detail="Alert not found")


# ---------- REST endpoints (kept for compatibility; the frontend uses GraphQL) ----------
# All alert routes require `Authorization: Bearer <token>` from /api/auth/signin or /api/auth/signup.

def _auth_or_400(fn, *args):
    try:
        return fn(*args)
    except db.AuthError as e:
        raise HTTPException(400, str(e))


@app.post("/api/auth/signup", response_model=AuthResponse, status_code=201)
def sign_up(body: SignUp):
    user = _auth_or_400(db.create_user, body.email, body.name, body.password)
    return {"token": db.create_session(user["id"]), "user": _user_out(user)}


@app.post("/api/auth/signin", response_model=AuthResponse)
def sign_in(body: SignIn):
    try:
        user = db.authenticate(body.email, body.password)
    except db.AuthError as e:
        raise HTTPException(401, str(e))
    return {"token": db.create_session(user["id"]), "user": _user_out(user)}


@app.post("/api/auth/signout", status_code=204)
def sign_out(authorization: Optional[str] = Header(None)):
    token = auth.token_from_header(authorization)
    if token:
        db.delete_session(token)


@app.get("/api/auth/me", response_model=User)
def me(user: dict = Depends(current_user)):
    return _user_out(user)


@app.get("/api/users", response_model=List[User], dependencies=[Depends(require(auth.USER_MANAGE))])
def list_users():
    return [_user_out(u) for u in db.list_users()]


@app.patch("/api/users/{user_id}", response_model=User)
def set_user_role(user_id: int, body: RoleUpdate, actor: dict = Depends(require(auth.USER_MANAGE))):
    try:
        return _user_out(db.set_user_role(user_id, body.role.value, actor))
    except db.NotFoundError:
        raise HTTPException(404, "User not found")
    except db.AuthError as e:
        raise HTTPException(400, str(e))


@app.get("/api/alerts", response_model=List[Alert], dependencies=[Depends(require(auth.ALERT_READ))])
def list_alerts(
    search: Optional[str] = Query(None, description="Match client name, address or raised by"),
    alert_type: Optional[AlertType] = None,
    status: Optional[AlertStatus] = None,
    country: Optional[str] = None,
):
    return db.list_alerts(
        search=search,
        alert_type=alert_type.value if alert_type else None,
        status=status.value if status else None,
        country=country,
    )


@app.get("/api/alerts/stats", dependencies=[Depends(require(auth.ALERT_READ))])
def alert_stats():
    return db.get_stats()


@app.get("/api/meta", dependencies=[Depends(require(auth.ALERT_READ))])
def meta():
    return db.get_meta()


@app.get("/api/alerts/{alert_id}", response_model=Alert, dependencies=[Depends(require(auth.ALERT_READ))])
def get_alert(alert_id: int):
    return _or_404(db.get_alert, alert_id)


@app.get("/api/alerts/{alert_id}/history", response_model=List[AuditEntry])
def alert_history(alert_id: int, user: dict = Depends(require(auth.ALERT_READ))):
    """Change history of one alert, newest first. Deleted alerts' history needs audit:read."""
    if not auth.can_view_history(user, alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    rows, _ = db.list_audit(entity_type="alert", entity_id=alert_id, limit=500)
    can_see_snapshots = auth.AUDIT_READ in auth.permissions_for(user)
    return [{**r, "snapshot": r["snapshot"] if can_see_snapshots else None} for r in rows]


@app.post("/api/alerts", response_model=Alert, status_code=201)
def create_alert(alert: AlertCreate, actor: dict = Depends(require(auth.ALERT_CREATE))):
    row, _ = db.create_alert(alert.model_dump(mode="json"), actor)
    return row


@app.patch("/api/alerts/{alert_id}", response_model=Alert)
def update_alert(alert_id: int, update: AlertUpdate, actor: dict = Depends(require(auth.ALERT_UPDATE))):
    row, _ = _or_404(db.update_alert, alert_id, update.model_dump(mode="json", exclude_unset=True), actor)
    return row


@app.delete("/api/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: int, actor: dict = Depends(require(auth.ALERT_DELETE))):
    _or_404(db.delete_alert, alert_id, actor)


# ---------- Audit log (audit:read) ----------

def audit_filters(
    entity_type: Optional[str] = Query(None, description="alert | user"),
    entity_id: Optional[int] = None,
    action: Optional[str] = Query(None, description="e.g. alert.update, user.role_change"),
    actor: Optional[str] = Query(None, description="Part of the actor's name"),
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> dict:
    if action and action not in db.AUDIT_ACTIONS:
        raise HTTPException(422, f"Invalid action '{action}'. Allowed: {', '.join(db.AUDIT_ACTIONS)}")
    return {
        "entity_type": entity_type, "entity_id": entity_id, "action": action, "actor": actor,
        "since": since.isoformat() if since else None, "until": until.isoformat() if until else None,
    }


@app.get("/api/audit", response_model=AuditPage, dependencies=[Depends(require(auth.AUDIT_READ))])
def audit_log(filters: dict = Depends(audit_filters),
              limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    rows, total = db.list_audit(**filters, limit=limit, offset=offset)
    return {"entries": rows, "total": total}


CSV_COLUMNS = ["id", "at", "actor_id", "actor_name", "action", "entity_type", "entity_id", "changes", "snapshot"]


def _csv_cell(value) -> str:
    if value is None:
        return ""
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    # Neutralise spreadsheet formula injection (client names etc. are user input).
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/api/audit.csv", dependencies=[Depends(require(auth.AUDIT_READ))])
def audit_csv(filters: dict = Depends(audit_filters)):
    """The whole filtered audit log as CSV (not paginated)."""
    def rows():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        for entry in db.iter_audit(**filters):
            writer.writerow([_csv_cell(entry[c]) for c in CSV_COLUMNS])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
        yield buf.getvalue()

    filename = f"audit-log-{date.today().isoformat()}.csv"
    return StreamingResponse(rows(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
