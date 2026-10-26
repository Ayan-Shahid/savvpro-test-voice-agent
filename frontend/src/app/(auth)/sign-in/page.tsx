import type { Metadata } from "next";
import { SignInForm } from "./form";

export const metadata: Metadata = { title: "Sign in" };

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return <SignInForm next={sp.next ?? null} notice={sp.notice ?? null} />;
}
