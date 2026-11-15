import { useEffect, useRef, useState } from "react";
import { api, matchesFilter, session } from "./api";
import AuditLog from "./AuditLog";
import { describeChanges, describeForTimeline, formatTime } from "./auditFormat";
import AuthPage from "./AuthPage";
import UserAdmin from "./UserAdmin";

const EMPTY_FORM = {
  clientName: "",
  dob: "",
  address: "",
  country: "",
  alertType: "AML",
  severity: "Medium",
  raisedBy: "",
  notes: "",
};

const PERM = {
  create: "alert:create",
  update: "alert:update",
  delete: "alert:delete",
  manageUsers: "user:manage",
  readAudit: "audit:read",
};

const slug = (s) => s.toLowerCase().replace(/\s+/g, "-");

function age(dob) {
  const d = new Date(dob);
  const now = new Date();
  let a = now.getFullYear() - d.getFullYear();
  if (now < new Date(now.getFullYear(), d.getMonth(), d.getDate())) a--;
  return a;
}

const THEME_KEY = "client-alerts-theme";

function initialTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {}
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function useTheme() {
  const [theme, setTheme] = useState(initialTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch {}
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

function ThemeSwitch({ theme, onToggle }) {
  const dark = theme === "dark";
  return (
    <button type="button" role="switch" aria-checked={dark} className="theme-switch"
      onClick={onToggle} title={`Switch to ${dark ? "light" : "dark"} mode`}>
      <span className="track"><span className="thumb" aria-hidden="true">{dark ? "🌙" : "☀️"}</span></span>
      <span>Dark mode</span>
    </button>
  );
}

function StatCard({ label, value, tone }) {
  return (
    <div className={`stat ${tone || ""}`}>
      <div className="stat-value">{value ?? 0}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function History({ entries }) {
  if (!entries) return <p className="muted">Loading…</p>;
  if (!entries.length) return <p className="muted">No recorded changes yet.</p>;
  return (
    <ol className="timeline">
      {entries.map((e) => (
        <li key={e.id} className={`tl-${e.action.split(".")[1]}`}>
          <div className="tl-head">{describeForTimeline(e)}</div>
          {e.action === "alert.update" && describeChanges(e).map((line, i) => <div key={i} className="tl-line">{line}</div>)}
          <time dateTime={e.at}>{formatTime(e.at)}</time>
        </li>
      ))}
    </ol>
  );
}

function DetailPanel({ alert, meta, saving, canUpdate, canDelete, history, onLoadHistory, onClose, onUpdate, onDelete }) {
  const [notes, setNotes] = useState(alert.notes || "");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  useEffect(() => {
    setNotes(alert.notes || "");
    setConfirmDelete(false);
  }, [alert.id, alert.notes]);
  useEffect(() => setShowHistory(false), [alert.id]);

  // History loads on demand (one request the first time it's expanded), then stays
  // current from the audit entries that mutations return.
  const toggleHistory = () => {
    if (!showHistory && !history) onLoadHistory(alert.id);
    setShowHistory(!showHistory);
  };

  return (
    <aside className="panel">
      <div className="panel-head">
        <h2>{alert.clientName}</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className="badges">
        <span className={`badge type-${slug(alert.alertType)}`}>{alert.alertType}</span>
        <span className={`badge sev-${slug(alert.severity)}`}>{alert.severity}</span>
        <span className={`badge status-${slug(alert.status)}`}>{alert.status}</span>
      </div>
      <dl className="details">
        <dt>Alert ID</dt><dd>#{alert.id}</dd>
        <dt>Date of birth</dt><dd>{alert.dob} <span className="muted">({age(alert.dob)} yrs)</span></dd>
        <dt>Address</dt><dd>{alert.address}</dd>
        <dt>Country</dt><dd>{alert.country}</dd>
        <dt>Type of alert</dt><dd>{alert.alertType}</dd>
        <dt>Raised by</dt><dd>{alert.raisedBy}</dd>
        <dt>Raised at</dt><dd>{new Date(alert.raisedAt).toLocaleString()}</dd>
      </dl>

      <label className="field">
        <span>Status</span>
        <select value={alert.status} disabled={saving || !canUpdate}
          onChange={(e) => onUpdate(alert.id, { status: e.target.value })}>
          {meta.statuses.map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      <label className="field">
        <span>Severity</span>
        <select value={alert.severity} disabled={saving || !canUpdate}
          onChange={(e) => onUpdate(alert.id, { severity: e.target.value })}>
          {meta.severities.map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      <label className="field">
        <span>Notes</span>
        <textarea rows={4} value={notes} readOnly={!canUpdate} onChange={(e) => setNotes(e.target.value)} />
      </label>
      {!canUpdate && <p className="hint">You have read-only access to alerts.</p>}
      <div className="row">
        {canUpdate && (
          <button className="btn" disabled={saving || notes === (alert.notes || "")}
            onClick={() => onUpdate(alert.id, { notes })}>Save notes</button>
        )}
        {!canDelete ? null : confirmDelete ? (
          <>
            <button className="btn danger" disabled={saving} onClick={() => onDelete(alert.id)}>Confirm delete</button>
            <button className="btn ghost" onClick={() => setConfirmDelete(false)}>Cancel</button>
          </>
        ) : (
          <button className="btn ghost danger-text" onClick={() => setConfirmDelete(true)}>Delete</button>
        )}
      </div>

      <section className="history">
        <button type="button" className="history-toggle" aria-expanded={showHistory} onClick={toggleHistory}>
          <span aria-hidden="true">{showHistory ? "▾" : "▸"}</span> History
        </button>
        {showHistory && <History entries={history} />}
      </section>
    </aside>
  );
}

function NewAlertForm({ meta, defaultRaisedBy, onCancel, onCreate }) {
  const [form, setForm] = useState({ ...EMPTY_FORM, raisedBy: defaultRaisedBy || "" });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await onCreate({ ...form, notes: form.notes || null });
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>Flag a client</h2>
        <div className="grid2">
          <label className="field"><span>Client name</span>
            <input required value={form.clientName} onChange={set("clientName")} /></label>
          <label className="field"><span>Date of birth</span>
            <input required type="date" value={form.dob} onChange={set("dob")} /></label>
          <label className="field span2"><span>Address</span>
            <input required value={form.address} onChange={set("address")} /></label>
          <label className="field"><span>Country</span>
            <input required value={form.country} onChange={set("country")} /></label>
          <label className="field"><span>Raised by</span>
            <input required value={form.raisedBy} onChange={set("raisedBy")} /></label>
          <label className="field"><span>Type of alert</span>
            <select value={form.alertType} onChange={set("alertType")}>
              {meta.alertTypes.map((t) => <option key={t}>{t}</option>)}
            </select></label>
          <label className="field"><span>Severity</span>
            <select value={form.severity} onChange={set("severity")}>
              {meta.severities.map((s) => <option key={s}>{s}</option>)}
            </select></label>
          <label className="field span2"><span>Notes</span>
            <textarea rows={3} value={form.notes} onChange={set("notes")} /></label>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="btn" disabled={submitting}>
            {submitting ? "Raising…" : "Raise alert"}
          </button>
        </div>
      </form>
    </div>
  );
}

function UserMenu({ user, onUsers, onAudit, onSignOut }) {
  if (!user) return null;
  return (
    <div className="user-menu">
      <span className="user-name" title={user.email}>{user.name}</span>
      <span className={`badge role-${user.role.toLowerCase()}`} aria-label={`Role: ${user.role}`}>{user.role}</span>
      {user.permissions.includes(PERM.manageUsers) && (
        <button className="btn ghost" onClick={onUsers}>Users</button>
      )}
      {user.permissions.includes(PERM.readAudit) && (
        <button className="btn ghost" onClick={onAudit}>Audit log</button>
      )}
      <button className="btn ghost" onClick={onSignOut}>Sign out</button>
    </div>
  );
}

function Dashboard({ user, onUser, onSignOut, onExpired, themeSwitch }) {
  const [alerts, setAlerts] = useState([]);
  const [stats, setStats] = useState(null);
  const [meta, setMeta] = useState(null);
  const [filters, setFilters] = useState({ search: "", alertType: "", status: "" });
  const [selected, setSelected] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showUsers, setShowUsers] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [histories, setHistories] = useState({}); // alertId -> audit entries (newest first)
  const initialised = useRef(false);
  const can = (perm) => !!user?.permissions.includes(perm);

  // Session expired / revoked: drop back to the sign-in page. Returns true if handled.
  const handleAuthError = (e) => {
    if (e?.code !== "UNAUTHENTICATED") return false;
    onExpired();
    return true;
  };

  // First run: one combined query (alerts + stats + meta).
  // Later filter changes: only re-query the list, debounced; stale requests are aborted.
  useEffect(() => {
    const ctrl = new AbortController();
    const first = !initialised.current;
    const run = async () => {
      try {
        if (first) {
          const data = await api.initialLoad(filters, { signal: ctrl.signal });
          onUser(data.me);
          setAlerts(data.alerts);
          setStats(data.stats);
          setMeta(data.meta);
          initialised.current = true;
        } else {
          setAlerts(await api.alerts(filters, { signal: ctrl.signal }));
        }
        setError("");
      } catch (e) {
        if (e.name === "AbortError" || handleAuthError(e)) return;
        setError(e.code ? e.message : `Could not reach the API: ${e.message}`);
      } finally {
        if (!ctrl.signal.aborted) setLoading(false);
      }
    };
    const t = setTimeout(run, first ? 0 : 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [filters]);

  // Merge a changed alert into the visible list without refetching.
  const applyAlert = (alert) => {
    setAlerts((list) => {
      const rest = list.filter((a) => a.id !== alert.id);
      if (!matchesFilter(alert, filters)) return rest;
      const exists = rest.length !== list.length;
      return exists ? list.map((a) => (a.id === alert.id ? alert : a)) : [alert, ...list];
    });
    setSelected((s) => (s && s.id === alert.id ? alert : s));
  };

  const loadHistory = async (id) => {
    try {
      const entries = await api.alertHistory(id);
      setHistories((h) => ({ ...h, [id]: entries }));
    } catch (e) {
      if (!handleAuthError(e)) setError(e.message);
    }
  };

  // Prepend a mutation's audit entry to that alert's history, if it has been loaded.
  const recordAudit = (entry) => {
    if (!entry) return;
    setHistories((h) => (h[entry.entityId] ? { ...h, [entry.entityId]: [entry, ...h[entry.entityId]] } : h));
  };

  const handleUpdate = async (id, input) => {
    setSaving(true);
    try {
      const { alert, stats, auditEntry } = await api.updateAlert(id, input);
      applyAlert(alert);
      recordAudit(auditEntry);
      setStats(stats);
      setError("");
    } catch (e) {
      if (!handleAuthError(e)) setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id) => {
    setSaving(true);
    try {
      const res = await api.deleteAlert(id);
      setAlerts((list) => list.filter((a) => a.id !== res.id));
      setStats(res.stats);
      setSelected(null);
      setError("");
    } catch (e) {
      if (!handleAuthError(e)) setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleCreate = async (input) => {
    let result;
    try {
      result = await api.createAlert(input);
    } catch (e) {
      if (!handleAuthError(e)) throw e;
      return;
    }
    const { alert, stats, auditEntry } = result;
    applyAlert(alert);
    setHistories((h) => ({ ...h, [alert.id]: auditEntry ? [auditEntry] : [] }));
    setStats(stats);
    setSelected(alert);
    setShowForm(false);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>Client Alerts</h1>
          <p className="muted">Flagged clients requiring compliance review</p>
        </div>
        <div className="header-actions">
          {themeSwitch}
          <UserMenu user={user} onUsers={() => setShowUsers(true)} onAudit={() => setShowAudit(true)} onSignOut={onSignOut} />
          {can(PERM.create) && (
            <button className="btn" disabled={!meta} onClick={() => setShowForm(true)}>+ Flag client</button>
          )}
        </div>
      </header>

      {stats && (
        <section className="stats">
          <StatCard label="Total alerts" value={stats.total} />
          <StatCard label="Open" value={stats.open} tone="warn" />
          <StatCard label="Under review" value={stats.underReview} tone="info" />
          <StatCard label="Closed" value={stats.closed} tone="ok" />
          <StatCard label="High severity (active)" value={stats.openHighSeverity} tone="danger" />
        </section>
      )}

      <section className="filters">
        <input
          aria-label="Search alerts"
          placeholder="Search name, address, raised by…"
          value={filters.search}
          onChange={(e) => setFilters({ ...filters, search: e.target.value })}
        />
        <select aria-label="Filter by alert type" value={filters.alertType} onChange={(e) => setFilters({ ...filters, alertType: e.target.value })}>
          <option value="">All alert types</option>
          {meta?.alertTypes.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select aria-label="Filter by status" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          <option value="">All statuses</option>
          {meta?.statuses.map((s) => <option key={s}>{s}</option>)}
        </select>
      </section>

      {error && <p className="error">{error}</p>}

      <main className={`content ${selected ? "with-panel" : ""}`}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Client</th>
                <th>DOB</th>
                <th>Address</th>
                <th>Country</th>
                <th>Alert type</th>
                <th>Severity</th>
                <th>Raised by</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={8} className="empty">Loading…</td></tr>}
              {!loading && alerts.length === 0 && (
                <tr><td colSpan={8} className="empty">No alerts match your filters.</td></tr>
              )}
              {alerts.map((a) => (
                <tr key={a.id} className={a.id === selected?.id ? "selected" : ""}
                  onClick={() => setSelected(a)}>
                  <td className="strong">{a.clientName}</td>
                  <td>{a.dob}</td>
                  <td className="addr">{a.address}</td>
                  <td>{a.country}</td>
                  <td><span className={`badge type-${slug(a.alertType)}`}>{a.alertType}</span></td>
                  <td><span className={`badge sev-${slug(a.severity)}`}>{a.severity}</span></td>
                  <td>{a.raisedBy}</td>
                  <td><span className={`badge status-${slug(a.status)}`}>{a.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {selected && meta && (
          <DetailPanel alert={selected} meta={meta} saving={saving}
            canUpdate={can(PERM.update)} canDelete={can(PERM.delete)}
            history={histories[selected.id]} onLoadHistory={loadHistory} onClose={() => setSelected(null)}
            onUpdate={handleUpdate} onDelete={handleDelete} />
        )}
      </main>

      {showForm && meta && can(PERM.create) && (
        <NewAlertForm meta={meta} defaultRaisedBy={user?.name} onCancel={() => setShowForm(false)} onCreate={handleCreate} />
      )}

      {showUsers && meta && can(PERM.manageUsers) && (
        <UserAdmin me={user} roles={meta.roles} onClose={() => setShowUsers(false)}
          onSelfUpdated={onUser} onError={handleAuthError} />
      )}

      {showAudit && meta && can(PERM.readAudit) && (
        <AuditLog actions={meta.auditActions} onClose={() => setShowAudit(false)} onError={handleAuthError} />
      )}
    </div>
  );
}

/**
 * Auth gate. With a stored token we go straight to the dashboard, whose single
 * InitialLoad request also returns `me` (so page load stays one request).
 * If that token turns out to be invalid, we fall back to the sign-in page.
 */
export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [signedIn, setSignedIn] = useState(() => !!session.get());
  const [user, setUser] = useState(null);
  const [notice, setNotice] = useState("");
  const themeSwitch = <ThemeSwitch theme={theme} onToggle={toggleTheme} />;

  const endSession = (message) => {
    session.set(null);
    setUser(null);
    setSignedIn(false);
    setNotice(message);
  };

  const handleAuth = ({ token, user }) => {
    session.set(token);
    setUser(user);
    setNotice("");
    setSignedIn(true);
  };

  const handleSignOut = async () => {
    try { await api.signOut(); } catch {} // token is dropped locally either way
    endSession("You have been signed out.");
  };

  if (!signedIn) return <AuthPage notice={notice} onAuth={handleAuth} themeSwitch={themeSwitch} />;
  return (
    <Dashboard user={user} onUser={setUser} onSignOut={handleSignOut} themeSwitch={themeSwitch}
      onExpired={() => endSession("Your session has expired. Please sign in again.")} />
  );
}
