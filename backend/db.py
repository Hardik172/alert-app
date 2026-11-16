"""Data access layer shared by the REST and GraphQL APIs."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("ALERTS_DB_PATH", Path(__file__).parent / "alerts.db"))


class AlertType(str, Enum):
    AML = "AML"
    SANCTIONS = "Sanctions"
    PEP = "PEP"
    FRAUD = "Fraud"
    ADVERSE_MEDIA = "Adverse Media"
    KYC = "KYC Incomplete"


class AlertStatus(str, Enum):
    OPEN = "Open"
    UNDER_REVIEW = "Under Review"
    CLOSED = "Closed"


class Severity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class Role(str, Enum):
    ADMIN = "Admin"
    ANALYST = "Analyst"
    AUDITOR = "Auditor"
    VIEWER = "Viewer"
    PENDING = "Pending"  # new sign-ups: no access until an Admin assigns a role


class NotFoundError(Exception):
    pass


class AuthError(Exception):
    """Bad credentials or invalid sign-up/role-change input. Message is safe to show users."""


UPDATABLE_FIELDS = {"status", "severity", "notes"}

SEED = [
    ("John Smith", "1980-04-12", "221B Baker Street, London NW1 6XE", "United Kingdom", "AML", "High", "Sarah Lee", "Open", "Multiple cash deposits just under reporting threshold."),
    ("Maria Garcia", "1975-09-30", "Calle de Alcalá 45, Madrid 28014", "Spain", "PEP", "Medium", "David Chen", "Under Review", "Spouse holds a ministerial position."),
    ("Ivan Petrov", "1968-01-05", "Tverskaya St 7, Moscow 125009", "Russia", "Sanctions", "High", "Compliance Bot", "Open", "Name match against OFAC SDN list (92%)."),
    ("Aisha Khan", "1990-06-21", "12 Marine Drive, Mumbai 400020", "India", "KYC Incomplete", "Low", "Priya Patel", "Open", "Proof of address expired."),
    ("Chen Wei", "1985-11-03", "88 Nanjing Rd, Shanghai 200001", "China", "Fraud", "High", "Fraud Engine", "Under Review", "Card used in 3 countries within 2 hours."),
    ("Lucas Müller", "1972-02-17", "Friedrichstraße 100, Berlin 10117", "Germany", "Adverse Media", "Medium", "Sarah Lee", "Closed", "Article resolved as false positive."),
    ("Fatima Al-Sayed", "1988-07-09", "Sheikh Zayed Rd, Dubai", "United Arab Emirates", "AML", "Medium", "David Chen", "Open", "Rapid movement of funds through new accounts."),
    ("Carlos Silva", "1995-12-25", "Av. Paulista 1000, São Paulo 01310-100", "Brazil", "Fraud", "Low", "Fraud Engine", "Closed", "Customer confirmed transactions."),
]

INSERT_SQL = """INSERT INTO alerts (client_name, dob, address, country, alert_type,
                severity, raised_by, status, notes, raised_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)"""


@contextmanager
def get_db(immediate: bool = False):
    """Open a connection; commits on success, rolls back on error.

    immediate=True takes the database write lock up front (BEGIN IMMEDIATE). Use it for
    check-then-write logic (last-admin guard, first-user-is-Admin, unique email) so two
    concurrent requests can't both pass the check before either one writes.
    """
    # Self-heal if the DB file was deleted or emptied while the server is running.
    needs_init = not DB_PATH.exists() or DB_PATH.stat().st_size == 0
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if needs_init:
            _create_schema(conn)
            conn.commit()
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as db:
        _create_schema(db)


def _create_schema(db):
    db.execute(
            """CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                dob TEXT NOT NULL,
                address TEXT NOT NULL,
                country TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                raised_by TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                raised_at TEXT NOT NULL
            )"""
    )
    db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
    )
    db.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            )"""
    )
    db.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                actor_id INTEGER,
                actor_name TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                changes TEXT,
                snapshot TEXT
            )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS audit_entity ON audit_log (entity_type, entity_id)")
    db.execute("CREATE INDEX IF NOT EXISTS audit_at ON audit_log (at)")
    # Append-only: the database itself refuses to edit or remove audit rows.
    for op in ("UPDATE", "DELETE"):
        db.execute(
            f"""CREATE TRIGGER IF NOT EXISTS audit_log_no_{op.lower()}
                BEFORE {op} ON audit_log
                BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END"""
        )
    if db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0:
        now = datetime.now().isoformat(timespec="seconds")
        db.executemany(INSERT_SQL, [row + (now,) for row in SEED])


def _fetch(db, alert_id: int) -> dict:
    row = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if not row:
        raise NotFoundError(f"Alert {alert_id} not found")
    return dict(row)


def list_alerts(search: Optional[str] = None, alert_type: Optional[str] = None,
                status: Optional[str] = None, country: Optional[str] = None) -> list:
    sql, params = "SELECT * FROM alerts WHERE 1=1", []
    if search:
        sql += " AND (client_name LIKE ? OR address LIKE ? OR raised_by LIKE ?)"
        params += [f"%{search}%"] * 3
    if alert_type:
        sql += " AND alert_type = ?"
        params.append(alert_type)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if country:
        sql += " AND country = ?"
        params.append(country)
    sql += " ORDER BY raised_at DESC, id DESC"
    with get_db() as db:
        return [dict(r) for r in db.execute(sql, params).fetchall()]


def alert_exists(alert_id: int) -> bool:
    with get_db() as db:
        return db.execute("SELECT 1 FROM alerts WHERE id = ?", (alert_id,)).fetchone() is not None


def get_alert(alert_id: int) -> dict:
    with get_db() as db:
        return _fetch(db, alert_id)


# Every write takes the acting user and records its audit entry on the SAME connection,
# so the change and its audit row commit (or roll back) together.

AUDITED_ALERT_FIELDS = ("client_name", "dob", "address", "country", "alert_type",
                        "severity", "raised_by", "status", "notes")


def create_alert(data: dict, actor: Optional[dict] = None) -> tuple:
    """Returns (row, audit_id)."""
    with get_db() as db:
        cur = db.execute(INSERT_SQL, (
            data["client_name"], data["dob"], data["address"], data["country"],
            data["alert_type"], data.get("severity") or Severity.MEDIUM.value,
            data["raised_by"], AlertStatus.OPEN.value, data.get("notes"),
            datetime.now().isoformat(timespec="seconds"),
        ))
        row = _fetch(db, cur.lastrowid)
        audit_id = _audit(db, actor, "alert.create", "alert", row["id"],
                          changes={k: [None, row[k]] for k in AUDITED_ALERT_FIELDS if row[k] is not None})
        return row, audit_id


def update_alert(alert_id: int, changes: dict, actor: Optional[dict] = None) -> tuple:
    """Returns (row, audit_id); audit_id is None when nothing actually changed."""
    changes = {k: v for k, v in changes.items() if k in UPDATABLE_FIELDS}
    with get_db() as db:
        before = _fetch(db, alert_id)
        diff = {k: [before[k], v] for k, v in changes.items() if before[k] != v}
        audit_id = None  # no-op updates leave no audit entry
        if diff:
            cols = ", ".join(f"{k} = ?" for k in diff)
            db.execute(f"UPDATE alerts SET {cols} WHERE id = ?", (*(v[1] for v in diff.values()), alert_id))
            audit_id = _audit(db, actor, "alert.update", "alert", alert_id, changes=diff)
        return _fetch(db, alert_id), audit_id


def delete_alert(alert_id: int, actor: Optional[dict] = None) -> int:
    """Delete an alert; returns the id of its audit entry (which keeps a full snapshot)."""
    with get_db() as db:
        row = _fetch(db, alert_id)
        db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        return _audit(db, actor, "alert.delete", "alert", alert_id, snapshot=row)


def get_stats() -> dict:
    with get_db() as db:
        by_status = dict(db.execute("SELECT status, COUNT(*) FROM alerts GROUP BY status").fetchall())
        by_type = dict(db.execute("SELECT alert_type, COUNT(*) FROM alerts GROUP BY alert_type").fetchall())
        high = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE severity = 'High' AND status != 'Closed'"
        ).fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    return {"total": total, "by_status": by_status, "by_type": by_type, "open_high_severity": high}


def get_meta() -> dict:
    return {
        "alert_types": [t.value for t in AlertType],
        "statuses": [s.value for s in AlertStatus],
        "severities": [s.value for s in Severity],
    }


# ---------- Users & sessions ----------

PBKDF2_ITERATIONS = 240_000
SESSION_TTL = timedelta(days=7)
MIN_PASSWORD_LENGTH = 8
# Upper bounds stop oversized input from bloating the append-only audit log
# or making PBKDF2 hash megabytes of data.
MAX_EMAIL_LENGTH = 254
MAX_NAME_LENGTH = 100
MAX_PASSWORD_LENGTH = 1024
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USER_COLUMNS = "id, email, name, role, created_at"


def hash_password(password: str, iterations: Optional[int] = None) -> str:
    iterations = iterations or PBKDF2_ITERATIONS
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iterations, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    except ValueError:
        return False
    return hmac.compare_digest(check, digest)


_dummy_hashes: dict = {}


def _dummy_hash() -> str:
    """A real hash to verify against when the email is unknown, so sign-in takes the same
    time whether or not the account exists (no email enumeration by timing)."""
    if PBKDF2_ITERATIONS not in _dummy_hashes:
        _dummy_hashes[PBKDF2_ITERATIONS] = hash_password(secrets.token_hex(16))
    return _dummy_hashes[PBKDF2_ITERATIONS]


def _token_hash(token: str) -> str:
    # Only a hash of each session token is stored, so a leaked DB can't be replayed.
    return hashlib.sha256(token.encode()).hexdigest()


def _user(db, user_id: int) -> dict:
    row = db.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise NotFoundError(f"User {user_id} not found")
    return dict(row)


def create_user(email: str, name: str, password: str, role: Optional[str] = None) -> dict:
    """Register a user. The very first account becomes Admin; everyone else starts as Viewer."""
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if len(email) > MAX_EMAIL_LENGTH:
        raise AuthError("Email is too long")
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address")
    if not name:
        raise AuthError("Name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise AuthError("Name is too long")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError("Password is too long")
    password_hash = hash_password(password)  # slow; do it before taking the write lock
    with get_db(immediate=True) as db:
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError("An account with this email already exists")
        if role is None:
            first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            role = Role.ADMIN.value if first else Role.PENDING.value
        try:
            cur = db.execute(
                "INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                (email, name, password_hash, role, datetime.now().isoformat(timespec="seconds")),
            )
        except sqlite3.IntegrityError:  # belt and braces: the UNIQUE index is the final word
            raise AuthError("An account with this email already exists")
        user = _user(db, cur.lastrowid)
        _audit(db, user, "auth.sign_up", "user", user["id"],
               changes={"email": [None, email], "role": [None, role]})
        return user


def authenticate(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    password = password or ""
    oversized = len(email) > MAX_EMAIL_LENGTH or len(password) > MAX_PASSWORD_LENGTH
    with get_db() as db:
        row = None if oversized else db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        # Always run one PBKDF2 check (against a dummy hash if there's no account) so the
        # response time doesn't reveal whether the email is registered.
        stored = row["password_hash"] if row else _dummy_hash()
        ok = not oversized and verify_password(password[:MAX_PASSWORD_LENGTH], stored) and row is not None
        if not ok:
            # Record the attempted email only (truncated) — never the password.
            _audit(db, None, "auth.sign_in_failed", "user", row["id"] if row else None,
                   changes={"email": [None, email[:MAX_EMAIL_LENGTH]]})
            failed = True
        else:
            user = {k: row[k] for k in ("id", "email", "name", "role", "created_at")}
            _audit(db, user, "auth.sign_in", "user", user["id"])
            failed = False
    if failed:  # raised after the with-block so the failed-attempt row is committed
        raise AuthError("Invalid email or password")
    return user


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + SESSION_TTL).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now().isoformat(timespec="seconds"),))
        db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?,?,?)",
                   (_token_hash(token), user_id, expires))
    return token


def user_for_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    with get_db() as db:
        row = db.execute(
            """SELECT u.id, u.email, u.name, u.role, u.created_at
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?""",
            (_token_hash(token), datetime.now().isoformat(timespec="seconds")),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    user = user_for_token(token)
    with get_db() as db:
        deleted = db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),)).rowcount
        if deleted and user:
            _audit(db, user, "auth.sign_out", "user", user["id"])


def list_users() -> list:
    with get_db() as db:
        return [dict(r) for r in db.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY id").fetchall()]


def set_user_role(user_id: int, role: str, actor: Optional[dict] = None) -> dict:
    if role not in [r.value for r in Role]:
        raise AuthError(f"Invalid role '{role}'. Allowed: {', '.join(r.value for r in Role)}")
    with get_db(immediate=True) as db:  # write lock: the last-admin count can't go stale
        user = _user(db, user_id)
        if user["role"] == Role.ADMIN.value and role != Role.ADMIN.value:
            admins = db.execute("SELECT COUNT(*) FROM users WHERE role = ?", (Role.ADMIN.value,)).fetchone()[0]
            if admins <= 1:
                raise AuthError("Cannot remove the last admin")
        if user["role"] != role:
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            _audit(db, actor, "user.role_change", "user", user_id,
                   changes={"role": [user["role"], role]},
                   snapshot={"email": user["email"], "name": user["name"]})
        return _user(db, user_id)


# ---------- Audit log (append-only) ----------

AUDIT_ACTIONS = [
    "alert.create", "alert.update", "alert.delete", "user.role_change",
    "auth.sign_up", "auth.sign_in", "auth.sign_in_failed", "auth.sign_out",
]
SYSTEM_ACTOR = "System"


def _audit(db, actor: Optional[dict], action: str, entity_type: str, entity_id: Optional[int],
           changes: Optional[dict] = None, snapshot: Optional[dict] = None) -> int:
    """Write one audit row using the caller's open connection (same transaction as the change)."""
    return db.execute(
        """INSERT INTO audit_log (at, actor_id, actor_name, action, entity_type, entity_id, changes, snapshot)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            datetime.now().isoformat(timespec="seconds"),
            actor["id"] if actor else None,
            actor["name"] if actor else SYSTEM_ACTOR,
            action, entity_type, entity_id,
            json.dumps(changes) if changes else None,
            json.dumps(snapshot) if snapshot else None,
        ),
    ).lastrowid


def _audit_row(row) -> dict:
    d = dict(row)
    d["changes"] = json.loads(d["changes"]) if d["changes"] else {}
    d["snapshot"] = json.loads(d["snapshot"]) if d["snapshot"] else None
    return d


def get_audit(audit_id: Optional[int]) -> Optional[dict]:
    if audit_id is None:
        return None
    with get_db() as db:
        row = db.execute("SELECT * FROM audit_log WHERE id = ?", (audit_id,)).fetchone()
    return _audit_row(row) if row else None


def list_audit(entity_type: Optional[str] = None, entity_id: Optional[int] = None,
               actor_id: Optional[int] = None, action: Optional[str] = None,
               actor: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
               limit: int = 50, offset: int = 0) -> tuple:
    """Return (rows, total) newest first. `actor` matches part of the actor's name;
    `since`/`until` are ISO dates or datetimes (inclusive)."""
    where, params = " WHERE 1=1", []
    if actor:
        where += " AND actor_name LIKE ?"
        params.append(f"%{actor}%")
    for col, val in (("entity_type", entity_type), ("entity_id", entity_id),
                     ("actor_id", actor_id), ("action", action)):
        if val is not None:
            where += f" AND {col} = ?"
            params.append(val)
    if since:
        where += " AND at >= ?"
        params.append(since)
    if until:
        # A bare date means "through the end of that day".
        where += " AND at <= ?"
        params.append(until + "T23:59:59" if len(until) == 10 else until)
    limit = max(1, min(int(limit), 500))
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM audit_log" + where, params).fetchone()[0]
        rows = db.execute("SELECT * FROM audit_log" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                          (*params, limit, max(0, int(offset)))).fetchall()
    return [_audit_row(r) for r in rows], total


def iter_audit(**filters):
    """All matching audit rows, newest first, fetched in pages (for CSV export)."""
    offset = 0
    while True:
        rows, _ = list_audit(**filters, limit=500, offset=offset)
        yield from rows
        if len(rows) < 500:
            return
        offset += 500
