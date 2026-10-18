"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api, onSessionLost } from "@/lib/api";
import { describeError } from "@/lib/messages";
import type { Me } from "@/lib/types";
import { useApi } from "@/lib/use-api";

interface Session {
  me: Me;
  can: (permission: string) => boolean;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6 text-sm text-muted-foreground">
      {children}
    </div>
  );
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { data, error } = useApi<Me>("/auth/me");
  const signedOut = error?.status === 401;

  // A missing, stale, or revoked session lands on sign-in and comes back here afterwards.
  // /login itself never redirects, so a stale cookie cannot cause a loop.
  useEffect(
    () =>
      onSessionLost(() => {
        const here = `${window.location.pathname}${window.location.search}`;
        router.replace(`/login?next=${encodeURIComponent(here)}`);
      }),
    [router],
  );

  if (error && !signedOut) return <Centered>{describeError(error)} Reload the page to try again.</Centered>;
  if (!data) return <Centered>Loading your workspace</Centered>;

  const session: Session = {
    me: data,
    can: (permission) => data.permissions.includes(permission),
    signOut: async () => {
      try {
        await api.post("/auth/logout");
      } finally {
        router.replace("/login");
      }
    },
  };
  return <SessionContext value={session}>{children}</SessionContext>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
