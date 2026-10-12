import Link from "next/link";

import Dashboard from "./Dashboard";

const MAX_APPID = 2 ** 31 - 1; // backend games.appid is a 32-bit integer

export default async function GamePage({ params }: { params: Promise<{ appid: string }> }) {
  const { appid: raw } = await params;
  const appid = Number(raw);
  if (!/^\d+$/.test(raw) || appid < 1 || appid > MAX_APPID) {
    return (
      <div className="panel state error" role="alert">
        <h1>&ldquo;{raw}&rdquo; is not a Steam appid</h1>
        <p className="muted">An appid is a positive whole number.</p>
        <Link href="/">Pick another game</Link>
      </div>
    );
  }
  return <Dashboard key={appid} appid={appid} />;
}
