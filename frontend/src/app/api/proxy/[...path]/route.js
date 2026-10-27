import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const maxDuration = 60; // a sleeping Render service needs ~30 s to wake up

// The browser talks only to this route. It adds the API key on the server, so the key never reaches
// the browser, and forwards the request to the FastAPI service.
async function forward(req, { params }) {
  const { path } = await params;
  const base = (process.env.API_URL || "").replace(/\/$/, "");
  const key = process.env.API_KEY;
  if (!base || !key) {
    return NextResponse.json({ detail: "API_URL and API_KEY are not set in this deployment's environment." }, { status: 500 });
  }

  const headers = {
    "x-api-key": key,
    "content-type": "application/json",
    // The visitor's address, so the fraud rules see the real source IP instead of Vercel's.
    "x-client-ip": (req.headers.get("x-forwarded-for") || "").split(",")[0].trim(),
  };
  const idempotencyKey = req.headers.get("idempotency-key");
  if (idempotencyKey) headers["idempotency-key"] = idempotencyKey;

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let upstream;
  try {
    upstream = await fetch(`${base}/${path.join("/")}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body: hasBody ? await req.text() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(55_000),
    });
  } catch {
    return NextResponse.json(
      { detail: "The API did not answer. If it was asleep it needs about 30 seconds to wake up. Try again." },
      { status: 504 },
    );
  }

  const out = { "content-type": upstream.headers.get("content-type") || "application/json" };
  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) out["retry-after"] = retryAfter;
  return new NextResponse(await upstream.text(), { status: upstream.status, headers: out });
}

export { forward as GET, forward as POST, forward as PATCH, forward as DELETE };
