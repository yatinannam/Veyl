"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Field, ErrorText, Input } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
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

export function LoginForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      await api.post("/auth/login", {
        tenant_slug: form.get("workspace"),
        email: form.get("email"),
        password: form.get("password"),
      });
      router.replace(safeNext(window.location.search));
    } catch (failure) {
      setError(describeError(failure));
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4">
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
