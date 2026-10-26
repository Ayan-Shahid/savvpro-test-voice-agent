"use client";
import { ErrorView } from "@/components/error-view";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <ErrorView code="500">
      <button onClick={reset} className="inline-flex h-11 items-center rounded-xl border border-white/15 bg-white/5 px-5 font-semibold hover:bg-white/10">Try again</button>
      {error.digest && <p className="w-full pt-2 font-mono text-xs text-mist-500">Reference {error.digest}</p>}
    </ErrorView>
  );
}
