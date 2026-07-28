import { useState, type FormEvent } from "react";

import { useNavigate } from "react-router";

import { apiRequest, type Session } from "../api/session";

export default function Login() {
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const session = await apiRequest<Session>("/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: data.get("username"),
          password: data.get("password"),
        }),
      });
      navigate(session.must_change_password ? "/account/password" : "/");
    } catch {
      setError(
        "The username or password was not accepted. Try again later if attempts are limited.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <form className="panel" onSubmit={submit}>
        <h1>Sign in</h1>
        {error && <p role="alert">{error}</p>}
        <label>
          Username
          <input name="username" autoComplete="username" required autoFocus />
        </label>
        <label>
          Password
          <input
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            required
          />
        </label>
        <label className="inline">
          <input
            type="checkbox"
            checked={showPassword}
            onChange={(event) => setShowPassword(event.target.checked)}
          />
          Show password
        </label>
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}
