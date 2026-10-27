// Login cookie. Uses only Web Crypto, so it runs in both the middleware (edge) and route handlers (node).
//
// The cookie holds an HMAC of the dashboard password, signed with SESSION_SECRET. Nothing is stored on
// the server; changing the password or the secret signs everyone out.

export const COOKIE = "fd_session";
export const MAX_AGE = 60 * 60 * 24 * 7; // one week

const encoder = new TextEncoder();

export async function expectedToken() {
  const secret = process.env.SESSION_SECRET;
  const password = process.env.DASHBOARD_PASSWORD;
  if (!secret || !password) return null; // not configured: nobody gets in
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(`fraud-desk:${password}`));
  return Array.from(new Uint8Array(signature), (b) => b.toString(16).padStart(2, "0")).join("");
}

// Compare without stopping at the first difference.
export function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
