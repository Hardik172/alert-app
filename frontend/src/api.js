// Minimal GraphQL client: one POST per operation, no extra dependencies.
const ENDPOINT = "/graphql";
export const TOKEN_KEY = "client-alerts-token";

// Session token (sent as a Bearer header). Kept in localStorage so a reload stays signed in.
export const session = {
  get() {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },
  set(token) {
    try { token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY); } catch {}
  },
};

/** Error carrying the server's `extensions.code` (UNAUTHENTICATED / FORBIDDEN). */
export class ApiError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}

async function gql(query, variables = {}, { signal } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = session.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify({ query, variables }),
    signal,
  });
  const body = await res.json().catch(() => null);
  if (body?.errors?.length) {
    throw new ApiError(body.errors.map((e) => e.message).join("; "), body.errors[0].extensions?.code);
  }
  if (!res.ok || !body) throw new Error(`Request failed (${res.status})`);
  return body.data;
}

const AUDIT_FIELDS = `
  fragment AuditFields on AuditEntry {
    id at actorId actorName action entityType entityId changes { field from to }
  }`;

const USER_FIELDS = `
  fragment UserFields on User { id email name role permissions }`;

const ALERT_FIELDS = `
  fragment AlertFields on Alert {
    id clientName dob address country alertType severity raisedBy status notes raisedAt
  }`;

const STATS_FIELDS = `
  fragment StatsFields on Stats {
    total open underReview closed openHighSeverity
  }`;

// Drop empty filter values so the server only sees active filters.
const cleanFilter = (f) =>
  Object.fromEntries(Object.entries(f).filter(([, v]) => v));

export const api = {
  // ----- auth -----
  signIn: (email, password) =>
    gql(
      `mutation SignIn($email: String!, $password: String!) {
        signIn(email: $email, password: $password) { token user { ...UserFields } }
      }
      ${USER_FIELDS}`,
      { email, password }
    ).then((d) => d.signIn),

  signUp: (input) =>
    gql(
      `mutation SignUp($input: SignUpInput!) {
        signUp(input: $input) { token user { ...UserFields } }
      }
      ${USER_FIELDS}`,
      { input }
    ).then((d) => d.signUp),

  signOut: () => gql(`mutation SignOut { signOut }`),

  /** Current user (works with no permissions, e.g. a Pending account). */
  me: () => gql(`query Me { me { ...UserFields } } ${USER_FIELDS}`).then((d) => d.me),

  users: () =>
    gql(`query Users { users { ...UserFields } } ${USER_FIELDS}`).then((d) => d.users),

  setUserRole: (id, role) =>
    gql(
      `mutation SetUserRole($id: Int!, $role: String!) {
        setUserRole(id: $id, role: $role) { ...UserFields }
      }
      ${USER_FIELDS}`,
      { id, role }
    ).then((d) => d.setUserRole),

  // ----- alerts -----

  /** Page load: current user + alerts + stats + dropdown options in ONE request. */
  initialLoad: (filter, opts) =>
    gql(
      `query InitialLoad($filter: AlertFilter) {
        me { ...UserFields }
        alerts(filter: $filter) { ...AlertFields }
        stats { ...StatsFields }
        meta { alertTypes statuses severities roles auditActions }
      }
      ${ALERT_FIELDS} ${STATS_FIELDS} ${USER_FIELDS}`,
      { filter: cleanFilter(filter) },
      opts
    ),

  /** Filter change: only the list (stats don't depend on filters). */
  alerts: (filter, opts) =>
    gql(
      `query Alerts($filter: AlertFilter) { alerts(filter: $filter) { ...AlertFields } }
      ${ALERT_FIELDS}`,
      { filter: cleanFilter(filter) },
      opts
    ).then((d) => d.alerts),

  /** Mutations return the changed alert + fresh stats, so no refetch is needed. */
  createAlert: (input) =>
    gql(
      `mutation CreateAlert($input: CreateAlertInput!) {
        createAlert(input: $input) {
          alert { ...AlertFields } stats { ...StatsFields } auditEntry { ...AuditFields }
        }
      }
      ${ALERT_FIELDS} ${STATS_FIELDS} ${AUDIT_FIELDS}`,
      { input }
    ).then((d) => d.createAlert),

  updateAlert: (id, input) =>
    gql(
      `mutation UpdateAlert($id: Int!, $input: UpdateAlertInput!) {
        updateAlert(id: $id, input: $input) {
          alert { ...AlertFields } stats { ...StatsFields } auditEntry { ...AuditFields }
        }
      }
      ${ALERT_FIELDS} ${STATS_FIELDS} ${AUDIT_FIELDS}`,
      { id, input }
    ).then((d) => d.updateAlert),

  deleteAlert: (id) =>
    gql(
      `mutation DeleteAlert($id: Int!) {
        deleteAlert(id: $id) { id stats { ...StatsFields } auditEntry { ...AuditFields } }
      }
      ${STATS_FIELDS} ${AUDIT_FIELDS}`,
      { id }
    ).then((d) => d.deleteAlert),

  // ----- audit -----

  /** One alert's change history, newest first. */
  alertHistory: (id, opts) =>
    gql(
      `query AlertHistory($id: Int!) { alertHistory(id: $id) { ...AuditFields } }
      ${AUDIT_FIELDS}`,
      { id },
      opts
    ).then((d) => d.alertHistory),

  /** Paginated, filtered audit log (audit:read). */
  auditLog: (filter, { limit, offset }, opts) =>
    gql(
      `query AuditLog($filter: AuditFilter, $limit: Int, $offset: Int) {
        auditLog(filter: $filter, limit: $limit, offset: $offset) {
          total entries { ...AuditFields snapshot }
        }
      }
      ${AUDIT_FIELDS}`,
      { filter: cleanFilter(filter), limit, offset },
      opts
    ).then((d) => d.auditLog),

  /** Whole filtered audit log as a CSV Blob (REST, since GraphQL can't stream files). */
  exportAuditCsv: async (filter) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(cleanFilter(filter))) {
      params.set(k.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`), v); // entityType -> entity_type
    }
    const headers = {};
    const token = session.get();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`/api/audit.csv?${params}`, { headers });
    if (res.status === 401) throw new ApiError("Not signed in", "UNAUTHENTICATED");
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    return res.blob();
  },
};

/** Client-side mirror of the server filter, used to keep the list correct after mutations. */
export function matchesFilter(alert, f) {
  if (f.alertType && alert.alertType !== f.alertType) return false;
  if (f.status && alert.status !== f.status) return false;
  if (f.search) {
    const q = f.search.toLowerCase();
    if (![alert.clientName, alert.address, alert.raisedBy].some((v) => v.toLowerCase().includes(q)))
      return false;
  }
  return true;
}
