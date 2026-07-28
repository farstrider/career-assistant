import { useEffect, useState } from "react";

import { useParams } from "react-router";

import { apiRequest } from "../api/session";
import type { AdminUser } from "./admin-users";

export default function AdminUserDetail() {
  const { userId } = useParams();
  const [user, setUser] = useState<AdminUser | null>(null);
  const [message, setMessage] = useState("");
  const [temporary, setTemporary] = useState("");

  async function refresh() {
    setUser(await apiRequest<AdminUser>(`/admin/users/${userId}`));
  }
  useEffect(() => void refresh(), [userId]);

  async function patch(values: Partial<AdminUser>) {
    if (!confirm("Apply this account change?")) return;
    try {
      setUser(
        await apiRequest<AdminUser>(`/admin/users/${userId}`, {
          method: "PATCH",
          body: JSON.stringify(values),
        }),
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Account update failed",
      );
    }
  }

  async function reset() {
    if (!confirm("Reset this password and revoke all sessions?")) return;
    const result = await apiRequest<{
      user: AdminUser;
      temporary_password: string;
    }>(`/admin/users/${userId}/password-reset`, { method: "POST" });
    setTemporary(result.temporary_password);
    setUser(result.user);
  }

  if (!user) return <p>Loading account…</p>;
  return (
    <>
      <h1>{user.username}</h1>
      <p>{user.display_name}</p>
      <p>
        Administrators can manage account metadata but cannot access this
        member’s career profile.
      </p>
      <p>The final active administrator cannot be disabled or demoted.</p>
      {message && <p role="alert">{message}</p>}
      {temporary && (
        <section className="notice">
          <strong>Temporary password (shown once)</strong>
          <code>{temporary}</code>
        </section>
      )}
      <div className="actions">
        <button onClick={() => void patch({ is_active: !user.is_active })}>
          {user.is_active ? "Disable" : "Enable"}
        </button>
        <button onClick={() => void patch({ is_admin: !user.is_admin })}>
          {user.is_admin ? "Remove administrator" : "Make administrator"}
        </button>
        <button onClick={() => void reset()}>Reset password</button>
        <button
          onClick={() =>
            void apiRequest(`/admin/users/${userId}/sessions/revoke`, {
              method: "POST",
            }).then(() => setMessage("Sessions revoked"))
          }
        >
          Revoke sessions
        </button>
      </div>
    </>
  );
}
