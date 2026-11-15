import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ACTION_LABELS, describeChanges, describeTarget, fieldLabel, formatTime } from "./auditFormat";

export const PAGE_SIZE = 25;
const EMPTY_FILTERS = { action: "", actor: "", since: "", until: "" };

function Snapshot({ snapshot }) {
  return (
    <details className="snapshot">
      <summary>Deleted record</summary>
      <dl className="details compact">
        {Object.entries(snapshot)
          .filter(([k]) => k !== "id")
          .map(([k, v]) => [<dt key={`${k}-t`}>{fieldLabel(k)}</dt>, <dd key={`${k}-d`}>{v ?? "—"}</dd>])}
      </dl>
    </details>
  );
}

/** Audit log (audit:read): filter, page through and export every recorded change. */
export default function AuditLog({ actions, onClose, onError }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState(null);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const typing = useRef(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const run = async () => {
      typing.current = false;
      try {
        const result = await api.auditLog(filters, { limit: PAGE_SIZE, offset }, { signal: ctrl.signal });
        setPage({ ...result, offset }); // range label follows the rows actually shown
        setError("");
      } catch (e) {
        if (e.name !== "AbortError" && !onError(e)) setError(e.message);
      }
    };
    // Debounce filter edits; load immediately on open and on page change.
    const t = setTimeout(run, typing.current ? 250 : 0);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [filters, offset]);

  const setFilter = (k) => (e) => {
    typing.current = true;
    setFilters({ ...filters, [k]: e.target.value });
    setOffset(0);
  };

  const exportCsv = async () => {
    setExporting(true);
    try {
      const blob = await api.exportAuditCsv(filters);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (!onError(e)) setError(e.message);
    } finally {
      setExporting(false);
    }
  };

  const total = page?.total ?? 0;
  const shownOffset = page?.offset ?? 0;
  const from = total ? shownOffset + 1 : 0;
  const to = Math.min(shownOffset + PAGE_SIZE, total);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal xwide" role="dialog" aria-labelledby="audit-title" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h2 id="audit-title">Audit log</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        <p className="muted">Every change, sign-in and role change, newest first. Entries can't be edited or deleted.</p>

        <div className="filters audit-filters">
          <select aria-label="Filter by action" value={filters.action} onChange={setFilter("action")}>
            <option value="">All actions</option>
            {actions.map((a) => <option key={a} value={a}>{ACTION_LABELS[a] || a}</option>)}
          </select>
          <input aria-label="Filter by user" placeholder="User name…" value={filters.actor} onChange={setFilter("actor")} />
          <label className="inline-field">From
            <input type="date" aria-label="From date" value={filters.since} onChange={setFilter("since")} /></label>
          <label className="inline-field">To
            <input type="date" aria-label="To date" value={filters.until} onChange={setFilter("until")} /></label>
          <button className="btn ghost" onClick={exportCsv} disabled={exporting}>
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        </div>

        {error && <p className="error" role="alert">{error}</p>}

        <div className="table-wrap">
          <table className="audit-table">
            <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
            <tbody>
              {!page && <tr><td colSpan={5} className="empty">Loading…</td></tr>}
              {page && page.entries.length === 0 && (
                <tr><td colSpan={5} className="empty">No audit entries match these filters.</td></tr>
              )}
              {page?.entries.map((e) => (
                <tr key={e.id} className="static">
                  <td>{formatTime(e.at)}</td>
                  <td className="strong">{e.actorName}</td>
                  <td><span className={`badge action-${e.action.split(".")[1]}`}>{ACTION_LABELS[e.action] || e.action}</span></td>
                  <td>{describeTarget(e)}</td>
                  <td className="audit-details">
                    {describeChanges(e).map((line, i) => <div key={i}>{line}</div>)}
                    {e.snapshot && e.action === "alert.delete" && <Snapshot snapshot={e.snapshot} />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="row pager">
          <span className="muted">{total ? `Showing ${from}–${to} of ${total}` : ""}</span>
          <button className="btn ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
          <button className="btn ghost" disabled={to >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
        </div>
      </div>
    </div>
  );
}
