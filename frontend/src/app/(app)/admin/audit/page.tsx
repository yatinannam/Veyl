"use client";

import { cn } from "cn";
import { useState } from "react";

import { ErrorText, Field, Input, Select } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { buildAuditQuery, EVENT_LABELS, eventDetail, NO_FILTERS, type AuditFilters } from "@/lib/audit";
import { formatDateTime } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { AuditEvent, AuditPage, UserInfo } from "@/lib/types";
import { useApi } from "@/lib/use-api";

const DECISION_LABEL = { allow: "Allowed", deny: "Denied", error: "Failed" } as const;

interface OlderPages {
  query: string;
  events: AuditEvent[];
  cursor: string | null;
}

export default function AuditPageView() {
  const users = useApi<UserInfo[]>("/users");
  const [filters, setFilters] = useState<AuditFilters>(NO_FILTERS);
  const [applied, setApplied] = useState<AuditFilters>(NO_FILTERS);
  const query = `/audit-events?${buildAuditQuery(applied)}`;
  const first = useApi<AuditPage>(query);
  // Older pages belong to the filters they were loaded for; new filters start over.
  const [older, setOlder] = useState<OlderPages | null>(null);
  const extra = older?.query === query ? older : null;
  const [error, setError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const emails = new Map((users.data ?? []).map((u) => [u.id, u.email]));
  const events = [...(first.data?.events ?? []), ...(extra?.events ?? [])];
  const cursor = extra ? extra.cursor : (first.data?.next_cursor ?? null);
  const loading = first.loading || loadingOlder;

  async function loadOlder() {
    if (!cursor) return;
    setLoadingOlder(true);
    setError(null);
    try {
      const page = await api.get<AuditPage>(`/audit-events?${buildAuditQuery(applied, cursor)}`);
      setOlder({ query, events: [...(extra?.events ?? []), ...page.events], cursor: page.next_cursor });
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setLoadingOlder(false);
    }
  }

  return (
    <div className="grid gap-6">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold tracking-tight">Audit log</h1>
        <p className="text-sm text-muted-foreground">
          Every question, sign-in, and permission change in this workspace. Entries cannot be edited
          or deleted. Questions are recorded as a reference code, not their text, unless the workspace
          opts in.
        </p>
      </div>
      <form
        className="grid gap-4 rounded-lg border border-border bg-card p-5 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_10rem_10rem_auto] lg:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(filters);
          setOlder(null);
          first.reload();
        }}
      >
        <Field label="Event" htmlFor="event-type">
          <Select id="event-type" value={filters.eventType} onChange={(e) => setFilters({ ...filters, eventType: e.target.value })}>
            <option value="">All events</option>
            {Object.entries(EVENT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Person" htmlFor="person">
          <Select id="person" value={filters.userId} onChange={(e) => setFilters({ ...filters, userId: e.target.value })}>
            <option value="">Everyone</option>
            {(users.data ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.email}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="From" htmlFor="from">
          <Input id="from" type="date" value={filters.from} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
        </Field>
        <Field label="To" htmlFor="to">
          <Input id="to" type="date" value={filters.to} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
        </Field>
        <Button type="submit">Show events</Button>
      </form>
      <ErrorText>{error ?? (first.error ? describeError(first.error) : null)}</ErrorText>
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border text-muted-foreground">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">When</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Event</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Outcome</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Who</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Detail</th>
              <th scope="col" className="px-4 py-2.5 font-medium">Trace</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id} className="border-b border-border last:border-b-0">
                <td className="px-4 py-2.5 whitespace-nowrap text-muted-foreground">{formatDateTime(event.occurred_at)}</td>
                <td className="px-4 py-2.5">{EVENT_LABELS[event.event_type] ?? event.event_type}</td>
                <td className={cn("px-4 py-2.5", event.decision !== "allow" && "text-destructive")}>
                  {DECISION_LABEL[event.decision]}
                </td>
                <td className="px-4 py-2.5">
                  {event.actor_user_id ? (emails.get(event.actor_user_id) ?? "Former member") : "System"}
                </td>
                <td className="px-4 py-2.5 text-muted-foreground">{eventDetail(event)}</td>
                <td className="px-4 py-2.5 text-muted-foreground" title={event.trace_id ?? undefined}>
                  {event.trace_id ? event.trace_id.slice(0, 8) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {events.length === 0 && !loading ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">No events match these filters.</p>
        ) : null}
      </div>
      {cursor ? (
        <div>
          <Button variant="outline" disabled={loading} onClick={() => void loadOlder()}>
            Show older events
          </Button>
        </div>
      ) : null}
    </div>
  );
}
