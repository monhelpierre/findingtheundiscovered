"use client";

import dynamic from "next/dynamic";
import { AppSplash } from "./Loader";

// MapLibre needs the browser, so the explorer is rendered client-side only.
const Explorer = dynamic(() => import("./Explorer"), {
  ssr: false,
  loading: () => <AppSplash message="Loading the explorer…" detail="Map engine and app code" />,
});

export default function ClientShell() {
  return <Explorer />;
}
