import type { Route } from "./+types/home";

export function meta(_: Route.MetaArgs) {
  return [{ title: "Overview | Career Assistant" }];
}

export default function Home() {
  return (
    <>
      <h1>Overview</h1>
      <p>Review newly acquired opportunities and record what happens next.</p>
    </>
  );
}
