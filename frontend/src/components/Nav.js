"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useApi } from "../lib/api";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/send", label: "Send money" },
  { href: "/transactions", label: "Transactions" },
  { href: "/accounts", label: "Accounts" },
  { href: "/fraud", label: "Fraud review" },
  { href: "/analysis", label: "Analysis" },
  { href: "/operations", label: "Operations" },
];

export default function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const { data: queue } = useApi("/fraud/queue", { refresh: 30_000 });
  const held = Array.isArray(queue) ? queue.length : 0;

  async function signOut() {
    await fetch("/api/session", { method: "DELETE" });
    router.replace("/login");
  }

  return (
    <>
      <nav className="nav" aria-label="Main">
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} aria-current={pathname === l.href ? "page" : undefined}>
            {l.label}
            {l.href === "/fraud" && held > 0 ? <span className="count" title="Transfers waiting for a decision">{held}</span> : null}
          </Link>
        ))}
      </nav>
      <div className="rail-foot">
        <button type="button" className="linklike" onClick={signOut}>
          Sign out
        </button>
      </div>
    </>
  );
}
