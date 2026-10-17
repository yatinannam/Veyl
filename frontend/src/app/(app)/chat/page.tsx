"use client";

import { cn } from "cn";
import { useState } from "react";

import { Answer } from "@/components/chat/answer";
import { Composer } from "@/components/chat/composer";
import { ErrorText } from "@/components/form";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { describeError } from "@/lib/messages";
import type { ConversationDetail, ConversationSummary, QueryResponse } from "@/lib/types";
import { useApi } from "@/lib/use-api";

export default function AskPage() {
  const conversations = useApi<ConversationSummary[]>("/conversations");
  const [activeId, setActiveId] = useState<string | null>(null);
  const detail = useApi<ConversationDetail>(activeId ? `/conversations/${activeId}` : null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const messages = activeId ? (detail.data?.messages ?? []) : [];

  async function ask(question: string) {
    setPending(question);
    setError(null);
    try {
      const answer = await api.post<QueryResponse>("/query", {
        question,
        conversation_id: activeId ?? undefined,
      });
      if (answer.conversation_id === activeId) detail.reload();
      else setActiveId(answer.conversation_id);
      conversations.reload();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(null);
    }
  }

  async function remove(id: string) {
    try {
      await api.delete(`/conversations/${id}`);
      if (id === activeId) setActiveId(null);
      conversations.reload();
    } catch (failure) {
      setError(describeError(failure));
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="space-y-3">
        <Button variant="outline" className="w-full" onClick={() => setActiveId(null)}>
          New conversation
        </Button>
        <nav aria-label="Conversations" className="space-y-1">
          {(conversations.data ?? []).map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              onClick={() => setActiveId(conversation.id)}
              aria-current={conversation.id === activeId ? "true" : undefined}
              className={cn(
                "block w-full rounded-md px-2.5 py-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/40",
                conversation.id === activeId ? "bg-accent" : "hover:bg-muted",
              )}
            >
              <span className="line-clamp-2 text-sm">{conversation.title}</span>
              <span className="text-xs text-muted-foreground">{formatDate(conversation.updated_at)}</span>
            </button>
          ))}
        </nav>
      </aside>

      <section aria-label="Conversation" className="flex min-h-[70vh] flex-col gap-6">
        <div className="flex-1 space-y-8">
          {messages.length === 0 && !pending ? (
            <div className="max-w-xl space-y-2 pt-6">
              <h1 className="text-xl font-semibold tracking-tight">Ask a question</h1>
              <p className="text-sm text-muted-foreground">
                Answers come only from documents you can read, with the sources listed under
                each answer. If nothing you can read covers the question, Veyl says so.
              </p>
            </div>
          ) : null}
          {messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className="max-w-2xl">
                <p className="text-xs font-medium text-muted-foreground">You asked</p>
                <p className="mt-1 text-base font-medium">{message.content}</p>
              </div>
            ) : (
              <article key={message.id} className="max-w-2xl">
                <Answer content={message.content} citations={message.citations} idPrefix={message.id} />
              </article>
            ),
          )}
          {pending ? (
            <div className="max-w-2xl space-y-2" aria-live="polite">
              <p className="text-xs font-medium text-muted-foreground">You asked</p>
              <p className="text-base font-medium">{pending}</p>
              <p className="text-sm text-muted-foreground">Searching the documents you can read</p>
            </div>
          ) : null}
        </div>
        <div className="sticky bottom-0 space-y-2 border-t border-border bg-background pt-4 pb-2">
          <ErrorText>{error}</ErrorText>
          <Composer onAsk={(question) => void ask(question)} disabled={pending !== null} />
          {activeId ? (
            <div className="flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => void remove(activeId)}>
                Delete this conversation
              </Button>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}
