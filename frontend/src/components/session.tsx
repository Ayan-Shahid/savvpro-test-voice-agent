"use client";
/**
 * Per-user state isolation in one browser.
 *
 * - Everything user-specific lives under <UserScope key={userId}>, so a different user remounts the whole
 *   subtree with empty state: nothing from the previous person survives in memory.
 * - The resource cache is created inside the scope (not a module singleton) for the same reason.
 * - Supabase auth events and a BroadcastChannel tell every tab when the signed-in user changes; a tab
 *   rendered for user A that sees user B (or no one) hard-reloads instead of showing A's data to B.
 */
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { supabaseBrowser } from "@/lib/supabase/client";
import { api, ApiError, SessionChanged } from "@/lib/api";

type Scope = {
  userId: string;
  email: string | null;
  call: <T>(path: string, init?: RequestInit) => Promise<T>;
  cache: Map<string, unknown>;
  inflight: Map<string, Promise<unknown>>;
  version: number;
  invalidate: (prefix?: string) => void;
  signOut: () => Promise<void>;
};

const ScopeCtx = createContext<Scope | null>(null);
const CHANNEL = "astrovoice-auth";

export function useScope(): Scope {
  const s = use(ScopeCtx);
  if (!s) throw new Error("useScope outside UserScope");
  return s;
}

function Scoped({ userId, email, children }: { userId: string; email: string | null; children: ReactNode }) {
  const router = useRouter();
  const [cache] = useState(() => new Map<string, unknown>());
  const [inflight] = useState(() => new Map<string, Promise<unknown>>());
  const [version, setVersion] = useState(0);

  const leave = useCallback((to: string) => {
    cache.clear();
    window.location.replace(to);
  }, [cache]);

  const call = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    try {
      return await api<T>(userId, path, init);
    } catch (e) {
      if (e instanceof SessionChanged) leave("/app");
      if (e instanceof ApiError && e.status === 401) leave(`/sign-in?next=${encodeURIComponent(location.pathname)}`);
      throw e;
    }
  }, [userId, leave]);

  const invalidate = useCallback((prefix?: string) => {
    for (const k of [...cache.keys()]) if (!prefix || k.startsWith(prefix)) cache.delete(k);
    setVersion((v) => v + 1);
  }, [cache]);

  const signOut = useCallback(async () => {
    await supabaseBrowser().auth.signOut({ scope: "local" });
    new BroadcastChannel(CHANNEL).postMessage({ type: "auth", userId: null });
    leave("/sign-in");
  }, [leave]);

  useEffect(() => {
    const sb = supabaseBrowser();
    const { data: sub } = sb.auth.onAuthStateChange((event, session) => {
      const now = session?.user.id ?? null;
      if (event === "SIGNED_OUT" || (now && now !== userId)) leave(now ? "/app" : "/sign-in");
    });
    const bc = new BroadcastChannel(CHANNEL);
    bc.onmessage = (m: MessageEvent<{ type: string; userId: string | null }>) => {
      if (m.data?.type === "auth" && m.data.userId !== userId) leave(m.data.userId ? "/app" : "/sign-in");
    };
    const onFocus = async () => {
      const { data } = await sb.auth.getSession();
      if ((data.session?.user.id ?? null) !== userId) leave(data.session ? "/app" : "/sign-in");
    };
    window.addEventListener("focus", onFocus);
    return () => {
      sub.subscription.unsubscribe();
      bc.close();
      window.removeEventListener("focus", onFocus);
    };
  }, [userId, leave, router]);

  const value = useMemo<Scope>(() => ({ userId, email, call, cache, inflight, version, invalidate, signOut }),
    [userId, email, call, cache, inflight, version, invalidate, signOut]);
  return <ScopeCtx value={value}>{children}</ScopeCtx>;
}

export function UserScope({ userId, email, children }: { userId: string; email: string | null; children: ReactNode }) {
  return <Scoped key={userId} userId={userId} email={email}>{children}</Scoped>;
}

/** Tiny per-user query hook: cached by key inside the scope, refetched after invalidate(). */
export function useResource<T>(key: string, path: string | null) {
  const { call, cache, inflight, version } = useScope();
  const [res, setRes] = useState<{ key: string; data?: T; error?: ApiError }>({ key });
  useEffect(() => {
    if (!path || cache.has(key)) return;
    let live = true;
    // De-duplicate: components asking for the same key at the same time share one request (no stampede).
    let p = inflight.get(key) as Promise<T> | undefined;
    if (!p) {
      p = call<T>(path).then((d) => { cache.set(key, d); return d; }).finally(() => inflight.delete(key));
      inflight.set(key, p);
    }
    p.then((d) => { if (live) setRes({ key, data: d }); })
      .catch((e: unknown) => { if (live && e instanceof ApiError) setRes({ key, error: e }); });
    return () => { live = false; };
  }, [key, path, version, call, cache, inflight]);
  const mine = res.key === key ? res : { key };
  const data = (cache.get(key) as T | undefined) ?? mine.data; // stale-while-revalidate after invalidate()
  return { data, error: mine.error, loading: data === undefined && !mine.error };
}

export function announceSignIn(userId: string) {
  try {
    new BroadcastChannel(CHANNEL).postMessage({ type: "auth", userId });
  } catch {
    /* old browsers: the focus check still catches it */
  }
}
