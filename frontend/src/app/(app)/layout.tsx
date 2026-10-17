import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { SessionProvider } from "@/components/session";

export default function SignedInLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
