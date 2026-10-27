"use client";

import { useCallback, useEffect, useState } from "react";

// Every call goes to our own /api/proxy route, which adds the API key on the server.
const BASE = "/api/proxy/api";

// Returns { ok, status, data } and never throws on HTTP errors (only on network failure).
export async function request(path, { method = "GET", body, headers } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "content-type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (res.status === 401 && typeof window !== "undefined") window.location.href = "/login";
  let data = null;
  try {
    data = await res.json();
  } catch {
    // empty or non-JSON body
  }
  return { ok: res.ok, status: res.status, data };
}

export function errorText(result) {
  const d = result.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => e.msg).join(". ");
  return `Something went wrong (HTTP ${result.status}).`;
}

// Like request(), but throws an Error with a readable message when the call fails.
export async function api(path, options) {
  const result = await request(path, options);
  if (!result.ok) throw new Error(errorText(result));
  return result.data;
}

// Load a GET endpoint, optionally refreshing it on a timer.
export function useApi(path, { refresh = 0 } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: true });

  const load = useCallback(
    async (quiet) => {
      if (!quiet) setState((s) => ({ ...s, loading: true }));
      try {
        const data = await api(path);
        setState({ data, error: null, loading: false });
      } catch (error) {
        setState((s) => ({ data: s.data, error, loading: false }));
      }
    },
    [path],
  );

  useEffect(() => {
    load(false);
    if (!refresh) return undefined;
    const id = setInterval(() => load(true), refresh);
    return () => clearInterval(id);
  }, [load, refresh]);

  return { ...state, reload: () => load(true) };
}
