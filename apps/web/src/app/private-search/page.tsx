import { Suspense } from "react";
import { PrivateTextSearch } from "@/components/private-text-search";

export default function PrivateSearchPage() {
  return <Suspense fallback={<p className="p-8">Loading private text search…</p>}><PrivateTextSearch /></Suspense>;
}
