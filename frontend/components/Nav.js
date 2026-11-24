"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getApiKey, setApiKey } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/new", label: "New Video" },
  { href: "/projects", label: "Projects" },
  { href: "/agents", label: "Agents" },
  { href: "/youtube", label: "YouTube" },
];

export default function Nav() {
  const pathname = usePathname();
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => setKey(getApiKey()), []);

  function save() {
    setApiKey(key);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <aside className="w-52 shrink-0">
      <div className="mb-8">
        <div className="text-lg font-semibold tracking-tight text-slate-100">
          AI Video <span className="text-amber">Agency</span>
        </div>
        <div className="mt-0.5 text-xs text-muted">autonomous production</div>
      </div>

      <nav className="space-y-1">
        {LINKS.map((link) => {
          const active =
            pathname === link.href ||
            (link.href !== "/" && pathname.startsWith(link.href));
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`block rounded-md px-3 py-2 text-sm transition-colors ${
                active
                  ? "bg-edge text-slate-100"
                  : "text-slate-400 hover:bg-edge/50 hover:text-slate-200"
              }`}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-8 border-t border-edge pt-4">
        <label className="label">API key</label>
        <input
          className="input font-mono text-xs"
          type="password"
          value={key}
          placeholder="X-API-Key"
          onChange={(e) => setKey(e.target.value)}
          onBlur={save}
        />
        <div className="mt-1 h-4 text-[11px] text-amber">{saved ? "saved" : ""}</div>
      </div>
    </aside>
  );
}
