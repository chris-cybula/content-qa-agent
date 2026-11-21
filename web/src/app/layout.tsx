// Root layout
// Page shell for every page: header with an API online/offline badge, and the global styles.

import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { apiUrl } from "@/lib/api";
import "@/styles/main.scss";

export const metadata: Metadata = {
  title: "Content QA Agent",
  description: "Review website content quality with AI",
};

async function ApiStatus() {
  await connection();
  let reachable = false;

  try {
    const response = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    reachable = response.ok;
  } catch {
    reachable = false;
  }

  return (
    <span className={`status-badge status-badge--${reachable ? "finished" : "failed"}`}>
      API {reachable ? "online" : "not reachable"}
    </span>
  );
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en-GB">
      <body className="layout">
        <header className="layout__header">
          <Link className="layout__brand" href="/">
            Content QA Agent
          </Link>
          <Suspense fallback={<span className="status-badge">API checking…</span>}>
            <ApiStatus />
          </Suspense>
        </header>
        <main className="layout__main">{children}</main>
      </body>
    </html>
  );
}
