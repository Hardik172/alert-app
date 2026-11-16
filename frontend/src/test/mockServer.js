// In-memory fake of the GraphQL backend, installed as global fetch.
// Records every request so tests can assert how many calls the UI makes.
import { vi } from "vitest";

export const SEED = [
  {
    id: 1, clientName: "John Smith", dob: "1980-04-12", address: "221B Baker Street, London",
    country: "United Kingdom", alertType: "AML", severity: "High", raisedBy: "Sarah Lee",
    status: "Open", notes: "Cash deposits under threshold.", raisedAt: "2026-11-01T10:00:00",
  },
  {
    id: 2, clientName: "Maria Garcia", dob: "1975-09-30", address: "Calle de Alcalá 45, Madrid",
    country: "Spain", alertType: "PEP", severity: "Medium", raisedBy: "David Chen",
    status: "Under Review", notes: null, raisedAt: "2026-11-02T10:00:00",
  },
  {
    id: 3, clientName: "Carlos Silva", dob: "1995-12-25", address: "Av. Paulista 1000, São Paulo",
    country: "Brazil", alertType: "Fraud", severity: "Low", raisedBy: "Fraud Engine",
    status: "Closed", notes: "Confirmed by customer.", raisedAt: "2026-11-03T10:00:00",
  },
];

export const META = {
  alertTypes: ["AML", "Sanctions", "PEP", "Fraud", "Adverse Media", "KYC Incomplete"],
  statuses: ["Open", "Under Review", "Closed"],
  severities: ["Low", "Medium", "High"],
  roles: ["Admin", "Analyst", "Auditor", "Viewer", "Pending"],
  auditActions: [
    "alert.create", "alert.update", "alert.delete", "user.role_change",
    "auth.sign_up", "auth.sign_in", "auth.sign_in_failed", "auth.sign_out",
  ],
};

export const PERMISSIONS = {
  Viewer: ["alert:read"],
  Analyst: ["alert:read", "alert:create", "alert:update"],
  Auditor: ["alert:read", "audit:read"],
  Admin: ["alert:read", "alert:create", "alert:update", "alert:delete", "user:manage", "audit:read"],
  Pending: [],
};

export const PASSWORD = "password123";
export const USERS = [
  { id: 1, email: "admin@test.io", name: "Ada Admin", role: "Admin" },
  { id: 2, email: "analyst@test.io", name: "Andy Analyst", role: "Analyst" },
  { id: 3, email: "viewer@test.io", name: "Vic Viewer", role: "Viewer" },
  { id: 4, email: "auditor@test.io", name: "Audrey Auditor", role: "Auditor" },
];

/** A pre-issued session token for each seeded role, e.g. tokenFor("Viewer"). */
export const tokenFor = (role) => `token-${role.toLowerCase()}`;

// Operation -> permission required (auth ops are public).
const REQUIRES = {
  InitialLoad: "alert:read", Alerts: "alert:read", CreateAlert: "alert:create",
  UpdateAlert: "alert:update", DeleteAlert: "alert:delete", Users: "user:manage", SetUserRole: "user:manage",
  AlertHistory: "alert:read", AuditLog: "audit:read", AuditCsv: "audit:read",
};

const toSnake = (k) => k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);

class GqlError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}

export function installMockServer({ alerts = SEED, audit: seedAudit = [] } = {}) {
  // ----- audit store (mirrors backend/db.py: newest first, snake_case field names) -----
  let audit = JSON.parse(JSON.stringify(seedAudit));
  let auditSeq = Math.max(0, ...audit.map((e) => e.id));
  const record = (actor, action, entityType, entityId, changes = {}, snapshot = null) => {
    const id = ++auditSeq;
    const entry = {
      id, at: `2026-11-09T12:${String(id % 60).padStart(2, "0")}:00`,
      actorId: actor?.id ?? null, actorName: actor?.name ?? "System",
      action, entityType, entityId,
      changes: Object.entries(changes).map(([field, [from, to]]) => ({ field, from, to })),
      snapshot,
    };
    audit.unshift(entry);
    return entry;
  };
  const listAudit = (f = {}) =>
    audit
      .filter((e) => !f.entityType || e.entityType === f.entityType)
      .filter((e) => f.entityId == null || e.entityId === f.entityId)
      .filter((e) => !f.action || e.action === f.action)
      .filter((e) => !f.actor || e.actorName.toLowerCase().includes(f.actor.toLowerCase()))
      .filter((e) => !f.since || e.at.slice(0, 10) >= f.since)
      .filter((e) => !f.until || e.at.slice(0, 10) <= f.until);

  let users = USERS.map((u) => ({ ...u, password: PASSWORD }));
  const sessions = new Map(users.map((u) => [tokenFor(u.role), u.id]));
  let sessionSeq = 0;
  const publicUser = ({ password, ...u }) => ({ ...u, permissions: PERMISSIONS[u.role] });
  const startSession = (u) => {
    const token = `token-session-${++sessionSeq}`;
    sessions.set(token, u.id);
    return { token, user: publicUser(u) };
  };

  let rows = JSON.parse(JSON.stringify(alerts));
  let nextId = Math.max(0, ...rows.map((r) => r.id)) + 1;
  const calls = [];
  const failures = {};

  const stats = () => ({
    total: rows.length,
    open: rows.filter((r) => r.status === "Open").length,
    underReview: rows.filter((r) => r.status === "Under Review").length,
    closed: rows.filter((r) => r.status === "Closed").length,
    openHighSeverity: rows.filter((r) => r.severity === "High" && r.status !== "Closed").length,
  });

  const list = (f = {}) =>
    rows
      .filter((r) => !f.alertType || r.alertType === f.alertType)
      .filter((r) => !f.status || r.status === f.status)
      .filter((r) => {
        if (!f.search) return true;
        const q = f.search.toLowerCase();
        return [r.clientName, r.address, r.raisedBy].some((v) => v.toLowerCase().includes(q));
      })
      .sort((a, b) => b.id - a.id);

  const find = (id) => {
    const row = rows.find((r) => r.id === id);
    if (!row) throw new Error(`Alert ${id} not found`);
    return row;
  };

  const handlers = {
    SignIn: ({ email, password }) => {
      const u = users.find((x) => x.email === email.trim().toLowerCase());
      if (!u || u.password !== password) throw new Error("Invalid email or password");
      return { signIn: startSession(u) };
    },
    SignUp: ({ input }) => {
      const email = input.email.trim().toLowerCase();
      if (users.some((u) => u.email === email)) throw new Error("An account with this email already exists");
      const u = { id: users.length + 1, email, name: input.name, password: input.password, role: "Pending" };
      users.push(u);
      return { signUp: startSession(u) };
    },
    Me: (_vars, { user }) => ({ me: user ? publicUser(user) : null }),
    SignOut: (_vars, { token }) => {
      sessions.delete(token);
      return { signOut: true };
    },
    Users: () => ({ users: users.map(publicUser) }),
    SetUserRole: ({ id, role }) => {
      const u = users.find((x) => x.id === id);
      if (u.role === "Admin" && role !== "Admin" && users.filter((x) => x.role === "Admin").length === 1) {
        throw new Error("Cannot remove the last admin");
      }
      u.role = role;
      return { setUserRole: publicUser(u) };
    },
    AlertHistory: ({ id }, { user }) => ({
      alertHistory: listAudit({ entityType: "alert", entityId: id }).map((e) => ({
        ...e, snapshot: PERMISSIONS[user.role].includes("audit:read") ? e.snapshot : null,
      })),
    }),
    AuditLog: ({ filter, limit = 50, offset = 0 }) => {
      const all = listAudit(filter);
      return { auditLog: { total: all.length, entries: all.slice(offset, offset + limit) } };
    },
    InitialLoad: ({ filter }, { user }) => ({
      me: publicUser(user), alerts: list(filter), stats: stats(), meta: META,
    }),
    Alerts: ({ filter }) => ({ alerts: list(filter) }),
    CreateAlert: ({ input }, { user }) => {
      const alert = {
        id: nextId++, severity: "Medium", notes: null, ...input,
        status: "Open", raisedAt: "2026-11-09T12:00:00",
      };
      rows.push(alert);
      const changes = Object.fromEntries(
        Object.entries(alert).filter(([k, v]) => !["id", "raisedAt"].includes(k) && v != null)
          .map(([k, v]) => [toSnake(k), [null, v]])
      );
      const auditEntry = record(user, "alert.create", "alert", alert.id, changes);
      return { createAlert: { alert, stats: stats(), auditEntry } };
    },
    UpdateAlert: ({ id, input }, { user }) => {
      const row = find(id);
      const diff = Object.fromEntries(
        Object.entries(input).filter(([k, v]) => row[k] !== v).map(([k, v]) => [toSnake(k), [row[k], v]])
      );
      Object.assign(row, input);
      const auditEntry = Object.keys(diff).length ? record(user, "alert.update", "alert", id, diff) : null;
      return { updateAlert: { alert: { ...row }, stats: stats(), auditEntry } };
    },
    DeleteAlert: ({ id }, { user }) => {
      const row = find(id);
      rows = rows.filter((r) => r.id !== id);
      const snapshot = Object.fromEntries(Object.entries(row).map(([k, v]) => [toSnake(k), v]));
      const auditEntry = record(user, "alert.delete", "alert", id, {}, snapshot);
      return { deleteAlert: { id, stats: stats(), auditEntry } };
    },
  };

  const authorize = (op, token) => {
    const user = users.find((u) => u.id === sessions.get(token));
    const perm = REQUIRES[op];
    if (perm && !user) throw new GqlError("Not signed in", "UNAUTHENTICATED");
    if (perm && !PERMISSIONS[user.role].includes(perm)) {
      throw new GqlError(`Your role (${user.role}) is not allowed to do this`, "FORBIDDEN");
    }
    return user;
  };

  // REST CSV export (GET, no body). Returns a Blob like the real endpoint.
  const csvExport = (url, token) => {
    const params = Object.fromEntries(new URL(url, "http://test").searchParams);
    calls.push({ op: "AuditCsv", variables: params, url, token });
    try {
      authorize("AuditCsv", token);
    } catch (e) {
      return { ok: false, status: e.code === "UNAUTHENTICATED" ? 401 : 403, blob: async () => new Blob([]) };
    }
    const filter = { action: params.action, actor: params.actor, since: params.since, until: params.until };
    const lines = ["id,at,actor_name,action", ...listAudit(filter).map((e) => [e.id, e.at, e.actorName, e.action].join(","))];
    return { ok: true, status: 200, blob: async () => new Blob([lines.join("\n")], { type: "text/csv" }) };
  };

  const fetch = vi.fn(async (url, options = {}) => {
    const token = options.headers?.Authorization?.replace(/^Bearer /, "") || null;
    if (url.startsWith("/api/audit.csv")) return csvExport(url, token);
    const { query, variables } = JSON.parse(options.body);
    const op = /(?:query|mutation)\s+(\w+)/.exec(query)[1];
    calls.push({ op, variables, url, token });
    const json = (body) => ({ ok: true, status: 200, json: async () => body });
    if (failures[op]) {
      const message = failures[op];
      delete failures[op];
      return json({ data: null, errors: [{ message }] });
    }
    try {
      const user = authorize(op, token);
      return json({ data: handlers[op](variables, { user, token }) });
    } catch (e) {
      const extensions = e.code ? { code: e.code } : undefined;
      return json({ data: null, errors: [{ message: e.message, extensions }] });
    }
  });

  vi.stubGlobal("fetch", fetch);

  return {
    fetch,
    calls,
    ops: () => calls.map((c) => c.op),
    failNext: (op, message) => { failures[op] = message; },
    expireSessions: () => sessions.clear(),
    /** Change a user's role behind the UI's back (e.g. an Admin approving a Pending sign-up). */
    setRole: (email, role) => { users.find((u) => u.email === email).role = role; },
    audit: () => audit,
  };
}
