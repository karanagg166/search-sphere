import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const origin = request.nextUrl.origin;
  const redirectUri = `${origin}/api/auth/callback/github`;
  const githubClientId =
    process.env.GITHUB_CLIENT_ID ||
    process.env.NEXT_PUBLIC_GITHUB_CLIENT_ID;

  if (githubClientId) {
    const params = new URLSearchParams({
      client_id: githubClientId,
      redirect_uri: redirectUri,
      scope: "read:user user:email",
    });

    return NextResponse.redirect(
      `https://github.com/login/oauth/authorize?${params.toString()}`
    );
  }

  const apiUrl = process.env.NEXT_PUBLIC_API_URL;
  const isLocalhost =
    request.nextUrl.hostname === "localhost" ||
    request.nextUrl.hostname === "127.0.0.1";

  if (apiUrl && (!apiUrl.includes("localhost") || isLocalhost)) {
    return NextResponse.redirect(`${apiUrl}/auth/github/login`);
  }

  return NextResponse.redirect(
    new URL(
      "/login?error=" +
        encodeURIComponent(
          "GitHub OAuth is not configured on this deployment. Please set GITHUB_CLIENT_ID in environment settings."
        ),
      request.url
    )
  );
}
