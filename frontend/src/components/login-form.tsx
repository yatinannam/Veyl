"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Field, ErrorText, Input } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { describeError } from "@/lib/messages";

/** Only same-site paths are allowed as a post-sign-in destination. */
export function safeNext(search: string): string {
  const next = new URLSearchParams(search).get("next");
  if (next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")) {
    return next;
  }
  return "/chat";
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
