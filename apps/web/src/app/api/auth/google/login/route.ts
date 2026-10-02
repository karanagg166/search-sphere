import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const origin = request.nextUrl.origin;
  const redirectUri = `${origin}/api/auth/callback/google`;
  const googleClientId =
    process.env.GOOGLE_CLIENT_ID ||
    process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

  if (googleClientId) {
    const params = new URLSearchParams({
      client_id: googleClientId,
      redirect_uri: redirectUri,
      response_type: "code",
      scope: "openid email profile",
      access_type: "offline",
      prompt: "consent",
    });

    return NextResponse.redirect(
      `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`
    );
  }

  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  const isLocalhost =
    request.nextUrl.hostname === "localhost" ||
    request.nextUrl.hostname === "127.0.0.1";

  if (apiUrl && (!apiUrl.includes("localhost") || isLocalhost)) {
    return NextResponse.redirect(`${apiUrl}/auth/google/login`);
  }

  return NextResponse.redirect(
    new URL(
      "/login?error=" +
        encodeURIComponent(
          "Google OAuth is not configured on this deployment. Please set GOOGLE_CLIENT_ID in environment settings."
        ),
      request.url
    )
  );
}
