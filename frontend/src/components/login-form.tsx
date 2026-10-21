"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Field, ErrorText, Input } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { DEMO_ACCOUNTS, type DemoAccount } from "@/lib/demo";
import { describeError } from "@/lib/messages";

/** Only same-site paths are allowed as a post-sign-in destination. The candidate is
 * resolved the way the router will resolve it (the URL parser drops tabs and newlines, so
 * "/<tab>/evil.example" becomes "//evil.example") and must keep this origin. */
export function safeNext(search: string, origin: string = window.location.origin): string {
  const next = new URLSearchParams(search).get("next");
  if (!next || !next.startsWith("/") || /[\u0000-\u001f\u007f\\]/.test(next)) return "/chat";
  const url = new URL(next, origin);
  if (url.origin !== origin) return "/chat";
  return `${url.pathname}${url.search}${url.hash}`;
}

interface Credentials {
  tenant_slug: FormDataEntryValue | null;
  email: FormDataEntryValue | null;
  password: FormDataEntryValue | null;
}

/** With `demoPassword`, the published demo accounts are offered as one-click sign-ins. */
export function LoginForm({ demoPassword }: { demoPassword?: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function signIn(credentials: Credentials) {
    setPending(true);
    setError(null);
    try {
      await api.post("/auth/login", credentials);
      router.replace(safeNext(window.location.search));
    } catch (failure) {
      setError(describeError(failure));
      setPending(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void signIn({
      tenant_slug: form.get("workspace"),
      email: form.get("email"),
      password: form.get("password"),
    });
  }

  return (
    <div className="grid gap-8">
      <SignInFields onSubmit={submit} error={error} pending={pending} />
      {demoPassword ? (
        <DemoAccounts
          password={demoPassword}
          pending={pending}
          onChoose={(account) =>
            void signIn({ tenant_slug: account.workspace, email: account.email, password: demoPassword })
          }
        />
      ) : null}
    </div>
  );
}

function DemoAccounts({
  password,
  pending,
  onChoose,
}: {
  password: string;
  pending: boolean;
  onChoose: (account: DemoAccount) => void;
}) {
  return (
    <section aria-labelledby="demo-heading" className="grid gap-3 border-t pt-6">
      <div className="space-y-1">
        <h2 id="demo-heading" className="text-sm font-semibold">
          Try the demo
        </h2>
        <p className="text-xs text-muted-foreground">
          Two companies with the same documents seen five ways. Every account uses the password{" "}
          <code className="rounded bg-muted px-1 py-0.5">{password}</code>. Changes are reset every
          night.
        </p>
      </div>
      <ul className="grid gap-2">
        {DEMO_ACCOUNTS.map((account) => (
          <li key={account.email}>
            <button
              type="button"
              disabled={pending}
              onClick={() => onChoose(account)}
              className="grid w-full gap-0.5 rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-60"
            >
              <span className="font-medium">
                Sign in as {account.name}{" "}
                <span className="font-normal text-muted-foreground">({account.role})</span>
              </span>
              <span className="text-xs text-muted-foreground">
                {account.workspace} / {account.email}: {account.sees}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SignInFields({
  onSubmit,
  error,
  pending,
}: {
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  error: string | null;
  pending: boolean;
}) {
  return (
    <form onSubmit={onSubmit} className="grid gap-4">
      <Field label="Workspace" htmlFor="workspace" hint="The short name your admin gave you, such as acme.">
        <Input id="workspace" name="workspace" autoComplete="organization" required />
      </Field>
      <Field label="Email" htmlFor="email">
        <Input id="email" name="email" type="email" autoComplete="username" required />
      </Field>
      <Field label="Password" htmlFor="password">
        <Input id="password" name="password" type="password" autoComplete="current-password" required />
      </Field>
      <ErrorText>{error}</ErrorText>
      <Button type="submit" size="lg" disabled={pending}>
        {pending ? "Signing in" : "Sign in"}
      </Button>
    </form>
  );
}
