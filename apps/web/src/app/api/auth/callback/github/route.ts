import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const code = searchParams.get("code");
  const error = searchParams.get("error");
  const errorDescription = searchParams.get("error_description");

  // Handle OAuth rejection or error from GitHub
  if (error) {
    const errorDetails = errorDescription || error;
    return NextResponse.redirect(
      new URL(
        `/auth/callback?error=${encodeURIComponent(errorDetails)}&provider=github`,
        request.url
      )
    );
  }

  if (!code) {
    return NextResponse.redirect(
      new URL(
        `/auth/callback?error=${encodeURIComponent("Authorization code missing from GitHub callback")}&provider=github`,
        request.url
      )
    );
  }

  const redirectUri = `${request.nextUrl.origin}/api/auth/callback/github`;

  const targetApis = [
    process.env.INTERNAL_API_URL,
    process.env.NEXT_PUBLIC_API_URL,
    "http://api:8000",
    "http://localhost:8000",
  ].filter(Boolean) as string[];

  const uniqueApis = Array.from(new Set(targetApis));
  let tokenData: { access_token?: string } | null = null;
  let exchangeError = "Failed to communicate with authentication service";

  for (const apiBase of uniqueApis) {
    try {
      const resp = await fetch(`${apiBase}/auth/oauth-exchange`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: "github",
          code,
          redirect_uri: redirectUri,
        }),
        cache: "no-store",
      });

      if (resp.ok) {
        tokenData = await resp.json();
        break;
      } else {
        const errorJson = await resp.json().catch(() => ({}));
        exchangeError = errorJson.detail || `OAuth exchange failed (HTTP ${resp.status})`;
        console.error("Backend GitHub OAuth exchange failed:", {
          status: resp.status,
          apiBase,
          error: exchangeError,
        });
        break;
      }
    } catch (err) {
      console.warn(`GitHub OAuth exchange attempt failed at ${apiBase}:`, err);
      continue;
    }
  }

  if (tokenData?.access_token) {
    return NextResponse.redirect(
      new URL(
        `/auth/callback?token=${encodeURIComponent(tokenData.access_token)}&provider=github`,
        request.url
      )
    );
  }

  return NextResponse.redirect(
    new URL(
      `/auth/callback?error=${encodeURIComponent(exchangeError)}&provider=github`,
      request.url
    )
  );
}
