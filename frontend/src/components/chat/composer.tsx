"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";

import { Textarea } from "@/components/form";
import { Button } from "@/components/ui/button";

export function Composer({ onAsk, disabled }: {
  onAsk: (question: string) => Promise<boolean>;
  disabled: boolean;
}) {
  const [question, setQuestion] = useState("");

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || disabled) return;
    setQuestion("");
    // Give the question back when it could not be answered, so it need not be retyped.
    if (!(await onAsk(trimmed))) setQuestion(trimmed);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form onSubmit={submit} className="flex items-end gap-2">
      <label htmlFor="question" className="sr-only">
        Your question
      </label>
      <Textarea
        id="question"
        rows={2}
        maxLength={2000}
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask about the documents you can read"
        className="min-h-12 resize-y"
      />
      <Button type="submit" size="lg" disabled={disabled || !question.trim()}>
        Ask
      </Button>
    </form>
  );
}
