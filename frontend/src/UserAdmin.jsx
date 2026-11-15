import { useEffect, useState } from "react";
import { api } from "./api";

export const ROLE_HELP = {
  Viewer: "Read-only",
  Analyst: "Create and update alerts",
  Auditor: "Read-only, plus the full audit log and CSV export",
  Admin: "Everything, incl. deleting alerts and managing users",
};

/** Admin-only: list users and change their role. */
export default function UserAdmin({ me, roles, onClose, onSelfUpdated, onError }) {
  const [users, setUsers] = useState(null);
  const [error, setError] = useState("");
  const [savingId, setSavingId] = useState(null);

  useEffect(() => {
    let live = true;
    api.users()
      .then((list) => live && setUsers(list))
      .catch((e) => live && (onError(e) || setError(e.message)));
    return () => { live = false; };
  }, []);

  const changeRole = async (user, role) => {
    setSavingId(user.id);
    setError("");
    try {
      const updated = await api.setUserRole(user.id, role);
      setUsers((list) => list.map((u) => (u.id === updated.id ? updated : u)));
      if (updated.id === me.id) onSelfUpdated(updated);
    } catch (e) {
      if (!onError(e)) setError(e.message);
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" role="dialog" aria-labelledby="users-title" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h2 id="users-title">Users &amp; roles</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        <ul className="role-legend">
          {roles.map((r) => <li key={r}><span className={`badge role-${r.toLowerCase()}`}>{r}</span> {ROLE_HELP[r]}</li>)}
        </ul>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead>
            <tbody>
              {!users && <tr><td colSpan={3} className="empty">Loading…</td></tr>}
              {users?.map((u) => (
                <tr key={u.id} className="static">
                  <td className="strong">{u.name}{u.id === me.id && <span className="muted"> (you)</span>}</td>
                  <td>{u.email}</td>
                  <td>
                    <select aria-label={`Role for ${u.email}`} value={u.role} disabled={savingId === u.id}
                      onChange={(e) => changeRole(u, e.target.value)}>
                      {roles.map((r) => <option key={r}>{r}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
