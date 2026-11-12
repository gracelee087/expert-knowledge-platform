"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getIdentity, setIdentity, type Role } from "@/lib/api";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/employees", label: "Mitarbeiter" },
  { href: "/interviews", label: "Interviews" },
];

const ROLE_LABEL: Record<Role, string> = { admin: "Administrator", interviewer: "Wissensmanager", expert: "Experte" };

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [role, setRole] = useState<Role>("admin");
  // Eingebettet im Helaba-Dashboard (iframe): Branding ausblenden, nur schlanke Tabs zeigen
  const [embedded, setEmbedded] = useState(false);
  useEffect(() => {
    setRole(getIdentity().role);
    setEmbedded(typeof window !== "undefined" && window.self !== window.top);
  }, []);

  return (
    <div className="min-h-screen">
      <header className={`sticky top-0 z-30 border-b border-black/[0.05] ${embedded ? "bg-canvas/95" : "bg-white/80"} backdrop-blur-xl`}>
        <div className={`mx-auto flex max-w-7xl items-center gap-8 px-6 ${embedded ? "h-12" : "h-14"}`}>
          <Link href="/" className={`flex items-center gap-2 ${embedded ? "hidden" : ""}`}>
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand text-[11px] font-bold text-white">EK</span>
            <span className="text-sm font-semibold tracking-tight">Expert Knowledge</span>
            <span className="hidden text-sm text-ink-3 sm:inline">· LBS Hessen-Thüringen</span>
          </Link>
          <nav className="flex gap-1">
            {NAV.map((n) => {
              const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
              return (
                <Link key={n.href} href={n.href}
                  className={`rounded-full px-3.5 py-1.5 text-sm transition-colors ${active ? (embedded ? "bg-white font-medium text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]" : "bg-canvas font-medium text-ink") : "text-ink-2 hover:text-ink"}`}>
                  {n.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs text-ink-2">
            <span className="hidden md:inline">Rolle (Demo)</span>
            <select
              value={role}
              onChange={(e) => {
                const r = e.target.value as Role;
                setIdentity(r, `demo-${r}`);
                setRole(r);
                location.reload();
              }}
              className="rounded-full border border-line bg-white px-3 py-1 text-xs"
            >
              {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                <option key={r} value={r}>{ROLE_LABEL[r]}</option>
              ))}
            </select>
          </div>
        </div>
      </header>
      <main className={`mx-auto max-w-7xl px-6 ${embedded ? "py-6" : "py-10"}`}>{children}</main>
    </div>
  );
}
