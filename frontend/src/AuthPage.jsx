import { useEffect, useState } from "react";
import { api } from "./api";

const MIN_PASSWORD = 8;
// Mirror the server's limits (backend/db.py MAX_*_LENGTH) so the browser stops oversized input early.
const MAX = { name: 100, email: 254, password: 1024 };
const modeFromHash = () => (window.location.hash === "#/signup" ? "signup" : "signin");

/** Sign in / Sign up. `#/signup` in the URL opens the sign-up form directly. */
export default function AuthPage({ notice, onAuth, themeSwitch }) {
  const [mode, setMode] = useState(modeFromHash);
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "" });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const signup = mode === "signup";

  useEffect(() => {
    const onHash = () => setMode(modeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const switchMode = (next) => {
    setMode(next);
    setError("");
    setForm((f) => ({ ...f, password: "", confirm: "" }));
    window.history.replaceState(null, "", `#/${next}`);
  };

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (signup && form.password.length < MIN_PASSWORD) {
      return setError(`Password must be at least ${MIN_PASSWORD} characters`);
    }
    if (signup && form.password !== form.confirm) return setError("Passwords do not match");
    setSubmitting(true);
    try {
      const result = signup
        ? await api.signUp({ name: form.name, email: form.email, password: form.password })
        : await api.signIn(form.email, form.password);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      onAuth(result);
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-theme">{themeSwitch}</div>
      <form className="auth-card" onSubmit={submit} noValidate>
        <div className="auth-brand">Client Alerts</div>
        <h1>{signup ? "Create your account" : "Sign in"}</h1>
        <p className="muted">
          {signup ? "Get access to the compliance alerts workspace." : "Welcome back. Sign in to review alerts."}
        </p>

        {notice && !error && <p className="notice" role="status">{notice}</p>}

        {signup && (
          <label className="field"><span>Full name</span>
            <input required autoComplete="name" maxLength={MAX.name} value={form.name} onChange={set("name")} /></label>
        )}
        <label className="field"><span>Email</span>
          <input required type="email" autoComplete="email" maxLength={MAX.email} value={form.email} onChange={set("email")} /></label>
        <label className="field"><span>Password</span>
          <input required type="password" autoComplete={signup ? "new-password" : "current-password"}
            maxLength={MAX.password} value={form.password} onChange={set("password")} /></label>
        {signup && (
          <label className="field"><span>Confirm password</span>
            <input required type="password" autoComplete="new-password" maxLength={MAX.password} value={form.confirm} onChange={set("confirm")} /></label>
        )}

        {error && <p className="error" role="alert">{error}</p>}

        <button type="submit" className="btn block" disabled={submitting}>
          {submitting ? (signup ? "Creating account…" : "Signing in…") : (signup ? "Create account" : "Sign in")}
        </button>

        {signup && (
          <p className="hint">New accounts start with read-only <strong>Viewer</strong> access. An admin can grant more.</p>
        )}

        <p className="auth-switch">
          {signup ? "Already have an account? " : "New here? "}
          <button type="button" className="link" onClick={() => switchMode(signup ? "signin" : "signup")}>
            {signup ? "Sign in" : "Create an account"}
          </button>
        </p>
      </form>
    </div>
  );
}
