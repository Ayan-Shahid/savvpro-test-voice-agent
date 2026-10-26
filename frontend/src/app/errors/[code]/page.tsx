import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ErrorView, ERRORS } from "@/components/error-view";
import { GalaxyBackground } from "@/components/galaxy";

export const metadata: Metadata = { robots: { index: false } };

/** Friendly pages for API failures the app redirects to (401, 402, 403, 429, 500, 502, 503). */
export default async function ErrorPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  if (!(code in ERRORS)) notFound();
  return <><GalaxyBackground /><ErrorView code={code} /></>;
}
