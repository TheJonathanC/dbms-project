import { NextResponse } from "next/server";
import { COOKIE, expectedToken, safeEqual } from "./lib/session";

// Every page and every /api/proxy call needs the login cookie.
export async function middleware(req) {
  const token = req.cookies.get(COOKIE)?.value;
  const expected = await expectedToken();
  if (expected && safeEqual(token, expected)) return NextResponse.next();

  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "Sign in required." }, { status: 401 });
  }
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  // everything except the login page, the login endpoint and static files
  matcher: ["/((?!login|api/session|_next/static|_next/image|favicon.ico).*)"],
};
