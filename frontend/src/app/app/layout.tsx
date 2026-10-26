import { redirect } from "next/navigation";
import { supabaseServer } from "@/lib/supabase/server";
import { UserScope } from "@/components/session";
import { AppShell } from "@/components/app-shell";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const supabase = await supabaseServer();
  const { data } = await supabase.auth.getUser();
  if (!data.user) redirect("/sign-in?next=/app");
  return (
    <UserScope userId={data.user.id} email={data.user.email ?? null}>
      <AppShell>{children}</AppShell>
    </UserScope>
  );
}
