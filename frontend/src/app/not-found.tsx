import type { Metadata } from "next";
import { ErrorView } from "@/components/error-view";
import { GalaxyBackground } from "@/components/galaxy";

export const metadata: Metadata = { title: "Page not found", robots: { index: false } };

export default function NotFound() {
  return <><GalaxyBackground /><ErrorView code="404" /></>;
}
