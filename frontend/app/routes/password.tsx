import { useState, type FormEvent } from "react";

import { useNavigate, useOutletContext } from "react-router";

import { apiRequest, type Session } from "../api/session";

export default function Password() {
  const session = useOutletContext<Session>();
  const navigate = useNavigate();
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setMessage("");
    try {
      await apiRequest<Session>("/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: data.get("current_password"),
          new_password: data.get("new_password"),
        }),
      });
      navigate("/");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Password change failed",
      );
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <h1>
        {session.must_change_password
          ? "Change your temporary password"
          : "Change password"}
      </h1>
      <p>Use 15–128 Unicode characters. Spaces are preserved.</p>
      {message && <p role="alert">{message}</p>}
      <label>
        Current password
        <input
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
        />
      </label>
      <label>
        New password
        <input
          name="new_password"
          type="password"
          autoComplete="new-password"
          minLength={15}
          maxLength={128}
          required
        />
      </label>
      <button>Change password</button>
    </form>
  );
}
