import { useOutletContext } from "react-router";

import type { Session } from "../api/session";

export default function Operations() {
  const session = useOutletContext<Session>();
  if (!session.roles.includes("admin"))
    return (
      <>
        <h1>Not authorized</h1>
        <p>Administrator access is required.</p>
      </>
    );
  return (
    <>
      <h1>Operations</h1>
      <p>PostgreSQL and Redis readiness are monitored by the platform.</p>
    </>
  );
}
