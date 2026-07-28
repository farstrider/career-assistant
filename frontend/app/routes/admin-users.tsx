import { useEffect, useState, type FormEvent } from "react";

import { Link } from "react-router";

import { apiRequest } from "../api/session";

export interface AdminUser {
  id: string;
  username: string;
  display_name: string;
  is_admin: boolean;
  is_active: boolean;
  must_change_password: boolean;
  created_at: string;
  last_login_at: string | null;
}

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [temporary, setTemporary] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    setUsers(await apiRequest<AdminUser[]>("/admin/users"));
  }
  useEffect(() => void refresh(), []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setError("");
    try {
      const result = await apiRequest<{
        user: AdminUser;
        temporary_password: string;
      }>("/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username: data.get("username"),
          display_name: data.get("display_name"),
          is_admin: data.get("is_admin") === "on",
          locale: data.get("locale"),
          timezone: data.get("timezone"),
        }),
      });
      setTemporary(result.temporary_password);
      form.reset();
      await refresh();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Account creation failed",
      );
    }
  }

  return (
    <>
      <h1>Accounts</h1>
      {temporary && (
        <section className="notice" aria-live="polite">
          <strong>Temporary password (shown once)</strong>
          <code>{temporary}</code>
          <button type="button" onClick={() => setTemporary("")}>
            Dismiss
          </button>
        </section>
      )}
      {error && <p role="alert">{error}</p>}
      <form className="panel" onSubmit={create}>
        <h2>Create account</h2>
        <label>
          Username
          <input name="username" required />
        </label>
        <label>
          Display name
          <input name="display_name" required />
        </label>
        <label>
          Locale
          <input name="locale" defaultValue="en" required />
        </label>
        <label>
          Timezone
          <input name="timezone" defaultValue="UTC" required />
        </label>
        <label className="inline">
          <input name="is_admin" type="checkbox" /> Administrator
        </label>
        <button>Create account</button>
      </form>
      <table>
        <thead>
          <tr>
            <th>Username</th>
            <th>Name</th>
            <th>State</th>
            <th>Role</th>
            <th>Last login</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>
                <Link to={`/admin/users/${user.id}`}>{user.username}</Link>
              </td>
              <td>{user.display_name}</td>
              <td>{user.is_active ? "Active" : "Disabled"}</td>
              <td>{user.is_admin ? "Administrator" : "Member"}</td>
              <td>
                {user.last_login_at
                  ? new Date(user.last_login_at).toLocaleString()
                  : "Never"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
