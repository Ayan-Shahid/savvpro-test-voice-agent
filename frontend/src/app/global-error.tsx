"use client";
import "./globals.css";
import { ErrorView } from "@/components/error-view";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <html lang="en"><body>
      <ErrorView code="500"><button onClick={reset} className="inline-flex h-11 items-center rounded-xl border border-white/15 px-5">Try again</button></ErrorView>
    </body></html>
  );
}
