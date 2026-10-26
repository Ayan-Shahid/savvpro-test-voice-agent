import Link from "next/link";
import { GalaxyBackground } from "@/components/galaxy";
import { AuthSphere } from "@/components/auth-sphere";
import { Logo } from "@/components/ui";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <GalaxyBackground />
      <div className="mx-auto grid min-h-dvh max-w-6xl items-center gap-8 px-6 py-10 lg:grid-cols-2">
        <div className="relative hidden h-[560px] lg:block">
          <AuthSphere />
          <p className="absolute bottom-4 left-4 max-w-xs text-sm text-mist-500">
            An armillary sphere: the rings astronomers used to model the sky before telescopes.
          </p>
        </div>
        <div className="mx-auto w-full max-w-md">
          <Link href="/" className="mb-8 inline-block"><Logo /></Link>
          <div className="glass p-7 sm:p-8">{children}</div>
        </div>
      </div>
    </>
  );
}
