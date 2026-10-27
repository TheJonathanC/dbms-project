import { NextResponse } from "next/server";
import { COOKIE, MAX_AGE, expectedToken, safeEqual } from "../../../lib/session";

export const dynamic = "force-dynamic";

// Sign in
export async function POST(req) {
  const token = await expectedToken();
  if (!token) {
    return NextResponse.json(
      { detail: "The dashboard password is not set. Add DASHBOARD_PASSWORD and SESSION_SECRET to the environment." },
      { status: 500 },
    );
  }
  let body = {};
  try {
    body = await req.json();
  } catch {
    // fall through with an empty body
  }
  if (!safeEqual(String(body.password ?? ""), process.env.DASHBOARD_PASSWORD)) {
    await new Promise((resolve) => setTimeout(resolve, 700)); // slow down guessing
    return NextResponse.json({ detail: "Wrong password." }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: MAX_AGE,
  });
  return res;
}

// Sign out
export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE, "", { path: "/", maxAge: 0 });
  return res;
}
