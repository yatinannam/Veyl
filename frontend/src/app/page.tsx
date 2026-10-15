export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-4 px-6">
      <h1 className="text-4xl font-semibold tracking-tight">Veyl</h1>
      <p className="text-muted-foreground text-lg">
        Permission-aware, multi-tenant retrieval-augmented generation. Answers come only from
        documents you are allowed to see.
      </p>
    </main>
  );
}
