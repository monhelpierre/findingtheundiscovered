import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

const DEFAULT_BACKEND = "http://127.0.0.1:8000";

/** Is a backend answering at this URL? (short timeout so `npm run dev` doesn't wait) */
async function backendUp(url: string): Promise<boolean> {
  try {
    const r = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(800) });
    return r.status < 500 || r.status === 503; // 503 = up, but export/db not ready: still forward
  } catch {
    return false;
  }
}

// Development: `npm run dev` forwards /api/** to the Django backend, exactly like Firebase Hosting
// forwards /api/** to Cloud Run in production. It does so when BACKEND_URL is set, or when a backend
// is already running at http://127.0.0.1:8000 at startup; otherwise the app uses the static data
// in public/data (start the backend, then restart `npm run dev`, to get sliders and notes).
// Production: `npm run build` writes a static site to out/ (Vercel, Firebase Hosting).
export default async function config(phase: string): Promise<NextConfig> {
  const base: NextConfig = {
    reactStrictMode: true,
    devIndicators: false,
    // Allow opening the dev server as http://127.0.0.1:<port> as well as localhost.
    allowedDevOrigins: ["127.0.0.1"],
    // Lets a build run while a dev server is using .next (e.g. NEXT_DIST_DIR=.next-build npm run build).
    distDir: process.env.NEXT_DIST_DIR || ".next",
  };
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    const explicit = process.env.BACKEND_URL?.replace(/\/$/, "");
    const backend = explicit ?? DEFAULT_BACKEND;
    if (explicit || (await backendUp(backend))) {
      console.log(`  /api -> ${backend} (backend)`);
      return { ...base, async rewrites() { return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }]; } };
    }
    console.log(`  No backend at ${backend}: using the static data in public/data. `
      + "Start the backend and restart `npm run dev` for weight sliders and notes.");
    return base;
  }
  return { ...base, output: "export", images: { unoptimized: true } };
}
