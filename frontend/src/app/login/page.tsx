import { ClassificationLevels } from "@/components/classification-levels";
import { LoginForm } from "@/components/login-form";

export default function LoginPage() {
  return (
    <main className="grid min-h-screen md:grid-cols-[minmax(0,1fr)_minmax(22rem,30rem)]">
      <section className="hidden flex-col justify-between bg-foreground p-10 text-background md:flex">
        <p className="text-lg font-semibold tracking-tight">Veyl</p>
        <div className="max-w-md space-y-8">
          <p className="font-serif text-3xl leading-snug">
            Answers from your company&apos;s documents, never beyond what you are cleared to read.
          </p>
          <ClassificationLevels />
        </div>
        <p className="max-w-md text-sm opacity-75">
          Every question is checked against document permissions before any text reaches the
          language model.
        </p>
      </section>
      <section className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm space-y-6">
          <div className="space-y-1">
            <p className="text-lg font-semibold tracking-tight md:hidden">Veyl</p>
            <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
            <p className="text-sm text-muted-foreground">Use the workspace your admin set up for you.</p>
          </div>
          <LoginForm />
        </div>
      </section>
    </main>
  );
}
