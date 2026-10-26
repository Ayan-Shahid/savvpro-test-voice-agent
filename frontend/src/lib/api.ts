"use client";
import { supabaseBrowser } from "@/lib/supabase/client";

export type ApiErrorBody = { code: string; message: string; request_id?: string; fields?: { field: string; message: string }[]; retry_after?: number };

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
}

/** Thrown when the browser's session now belongs to someone else (another tab signed in as another user). */
export class SessionChanged extends Error {}

/**
 * Calls the API with the current access token. `expectedUser` pins every request to the user this page was
 * rendered for: if the cookie session switched to another account in another tab, we refuse to send the
 * request (and the scope reloads) instead of mixing two people's data on one screen.
 */
export async function api<T>(expectedUser: string, path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabaseBrowser().auth.getSession();
  const session = data.session;
  if (!session) throw new SessionChanged("signed out");
  if (session.user.id !== expectedUser) throw new SessionChanged("user changed");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${session.access_token}`);
  headers.set("Accept", "application/json");
  headers.set("X-Request-ID", crypto.randomUUID().replaceAll("-", ""));
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers, credentials: "omit", cache: "no-store", redirect: "error" });
  if (res.status === 204) return undefined as T;
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, json?.error ?? { code: "network", message: "Something went wrong. Please try again." });
  }
  return json as T;
}
