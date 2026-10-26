import type { Metadata } from "next";
import { SignUpForm } from "./form";

export const metadata: Metadata = { title: "Create account" };

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  return <SignUpForm next={(await searchParams).next ?? null} />;
}
