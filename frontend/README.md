# Veyl frontend

Next.js (App Router) + TypeScript + Tailwind CSS + shadcn/ui. See the [root README](../README.md) for the full project.

```bash
pnpm install
pnpm dev          # http://localhost:3000 — src/proxy.ts forwards /api/* to API_INTERNAL_URL (default http://localhost:8000), read at request time
pnpm test         # Vitest
pnpm lint && pnpm typecheck
pnpm build
```
