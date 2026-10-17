"use client";

import { cn } from "cn";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { ClearanceMeter } from "@/components/classification";
import { useSession } from "@/components/session";
import { Button } from "@/components/ui/button";
import { CLASSIFICATION_LABEL, CLEARANCE, ROLE_LABEL } from "@/lib/types";

export interface NavItem {
  href: string;
  label: string;
}

/** Navigation hints from the permission list. The API enforces every page's data. */
export function navItems(permissions: string[]): NavItem[] {
  const items: NavItem[] = [
    { href: "/chat", label: "Ask" },
    { href: "/documents", label: "Documents" },
  ];
  if (permissions.includes("user.manage")) items.push({ href: "/admin/users", label: "People" });
  if (permissions.includes("audit.read")) items.push({ href: "/admin/audit", label: "Audit log" });
  return items;
}

export function AppShell({ children }: { children: ReactNode }) {
  const { me, signOut } = useSession();
  const pathname = usePathname();
  const clearance = CLEARANCE[me.user.role];

  return (
    <div className="grid min-h-screen grid-cols-1 md:grid-cols-[13rem_minmax(0,1fr)]">
      <aside className="border-b border-border bg-card md:border-r md:border-b-0">
        <div className="px-5 pt-5 pb-3">
          <p className="text-lg font-semibold tracking-tight">Veyl</p>
          <p className="truncate text-sm text-muted-foreground">{me.tenant.name}</p>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col">
          {navItems(me.permissions).map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
                  active
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card px-6 py-3">
          <div className="flex items-center gap-3">
            <ClearanceMeter level={clearance} />
            <p className="text-sm">
              <span className="font-medium">{ROLE_LABEL[me.user.role]}</span>
              <span className="text-muted-foreground">
                {" "}
                can read up to {CLASSIFICATION_LABEL[clearance].toLowerCase()}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-muted-foreground sm:inline">{me.user.email}</span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        </header>
        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6">{children}</main>
      </div>
    </div>
  );
}
