"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Field } from "../../components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        router.replace("/");
        router.refresh();
        return;
      }
      const data = await res.json().catch(() => ({}));
      setError(data.detail || "Could not sign in.");
    } catch {
      setError("Could not reach the server. Check your connection and try again.");
    }
    setBusy(false);
  }

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit}>
        <div>
          <div className="brand-name">Fraud Desk</div>
          <div className="brand-sub">Bank Fraud System. Sign in to review transfers and accounts.</div>
        </div>
        <Field label="Password">
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {error ? <div className="notice bad" role="alert">{error}</div> : null}
        <Button variant="primary" type="submit" disabled={busy || !password}>
          {busy ? "Signing in..." : "Sign in"}
        </Button>
      </form>
    </main>
  );
}
