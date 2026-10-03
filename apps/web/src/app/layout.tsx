import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { BackendStatusBanner } from "@/components/backend-status-banner";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Search Sphere | Semantic Search & RAG Platform",
  description: "Search Sphere - Scalable semantic search and RAG platform",
  icons: {
    icon: [
      { url: "/favicon.ico" },
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon-96x96.png", sizes: "96x96", type: "image/png" },
    ],
    shortcut: "/favicon.ico",
    apple: "/apple-touch-icon.png",
  },
  manifest: "/site.webmanifest",
  appleWebApp: {
    title: "Search Sphere",
    statusBarStyle: "default",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={inter.className}>
        <Providers>
          {children}
          <BackendStatusBanner />
        </Providers>
      </body>
    </html>
  );
}
