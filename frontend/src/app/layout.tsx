import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "AstroVoice — book telescope time by voice", template: "%s · AstroVoice" },
  description: "Book time on the Meridian Ridge University Observatory telescopes by talking to a deterministic voice agent.",
  robots: { index: true, follow: true },
  referrer: "strict-origin-when-cross-origin",
};

export const viewport: Viewport = { themeColor: "#05060d", colorScheme: "dark" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  await connection(); // render per request so every page gets the CSP nonce from src/proxy.ts
  return (
    <html lang="en">
      <body className="min-h-dvh">{children}</body>
    </html>
  );
}
