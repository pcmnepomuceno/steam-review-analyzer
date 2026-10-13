import Link from "next/link";

// Replaces Next's default 404, whose 100vh box inside <main> pushed the footer below the fold.
export default function NotFound() {
  return (
    <div className="panel state error">
      <h1>Page not found</h1>
      <div className="muted">
        <p>There is nothing at this address.</p>
        <p>
          <Link href="/">Back to the homepage</Link>
        </p>
      </div>
    </div>
  );
}
