// Turns raw audit entries into short human-readable text. Shared by the
// alert History timeline and the Audit log screen.

export const ACTION_LABELS = {
  "alert.create": "Alert created",
  "alert.update": "Alert updated",
  "alert.delete": "Alert deleted",
  "user.role_change": "Role changed",
  "auth.sign_up": "Signed up",
  "auth.sign_in": "Signed in",
  "auth.sign_in_failed": "Failed sign-in",
  "auth.sign_out": "Signed out",
};

const FIELD_LABELS = { client_name: "client name", alert_type: "alert type", raised_by: "raised by", dob: "date of birth" };

export const fieldLabel = (f) => FIELD_LABELS[f] || f.replace(/_/g, " ");

export const formatTime = (iso) =>
  new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

const LONG = new Set(["notes", "address"]);
const shorten = (v, n = 60) => (v && v.length > n ? `${v.slice(0, n - 1)}…` : v);

/** One line per changed field, e.g. "status: Open → Closed". */
export function describeChanges(entry) {
  if (entry.action === "alert.create") return [];
  if (entry.action === "auth.sign_in_failed") {
    const email = entry.changes.find((c) => c.field === "email")?.to;
    return email ? [`attempted as ${email}`] : [];
  }
  return entry.changes.map(({ field, from, to }) => {
    const label = fieldLabel(field);
    if (LONG.has(field)) return to ? `${label}: “${shorten(to)}”` : `${label} cleared`;
    if (from == null) return `${label}: ${to}`;
    return `${label}: ${from ?? "—"} → ${to ?? "—"}`;
  });
}

/** Headline for the per-alert timeline, e.g. "Ada Admin changed status". */
export function describeForTimeline(entry) {
  const who = entry.actorName;
  switch (entry.action) {
    case "alert.create": return `${who} raised this alert`;
    case "alert.delete": return `${who} deleted this alert`;
    case "alert.update": return `${who} changed ${entry.changes.map((c) => fieldLabel(c.field)).join(", ")}`;
    default: return `${who}: ${ACTION_LABELS[entry.action] || entry.action}`;
  }
}

export function describeTarget(entry) {
  if (entry.entityId == null) return "—";
  if (entry.entityType === "alert") return `Alert #${entry.entityId}`;
  const email = entry.snapshot?.email;
  return `User #${entry.entityId}${email ? ` (${email})` : ""}`;
}
