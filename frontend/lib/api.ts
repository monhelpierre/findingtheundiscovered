// Talks to the Django backend under /api/ (same origin: forwarded by `next dev` locally and by Firebase
// Hosting in production). If the backend is unreachable, data falls back to the static copy in /data/,
// so the map always works; only the backend features (weights, notes) need it.
import type { Candidate, Tier } from "./types";

export type Source = "api" | "static";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { cache: "no-store", ...init });
  const type = r.headers.get("content-type") ?? "";
  if (!type.includes("json")) throw new Error(`${url}: not JSON (HTTP ${r.status})`);
  const body = await r.json();
  if (!r.ok) throw new Error(body?.error ?? `${url}: HTTP ${r.status}`);
  return body as T;
}

/** Try the API first, then the static export. */
export async function load<T>(apiPath: string, staticPath: string): Promise<{ data: T; source: Source }> {
  try {
    return { data: await json<T>(apiPath), source: "api" };
  } catch {
    return { data: await json<T>(staticPath), source: "static" };
  }
}

export type ScoreResult = {
  region: string;
  target: string;
  weights: Record<string, number>;
  exclusion_km: number;
  columns: { score: (number | null)[]; tier: (Tier | null)[]; met: number[]; scored: number[] } & Record<string, unknown>;
  candidates: Candidate[];
};

export function scoreRegion(target: string, region: string, weights: Record<string, number>, signal?: AbortSignal) {
  const q = new URLSearchParams({ target, region });
  for (const [k, w] of Object.entries(weights)) q.set(`w.${k}`, String(w));
  return json<ScoreResult>(`/api/score?${q}`, { signal });
}

export type Note = {
  id: number; target_id: string; region: string; cell: number; lat: number; lon: number;
  author: string; text: string; created_at: string;
};

export function getNotes(region: string, cell: number) {
  return json<Note[]>(`/api/notes?${new URLSearchParams({ region, cell: String(cell) })}`);
}

export function addNote(note: Omit<Note, "id" | "created_at">, token: string) {
  return json<Note>("/api/notes", {
    method: "POST",
    headers: { "content-type": "application/json", "x-team-token": token },
    body: JSON.stringify(note),
  });
}

/** Per-browser convenience storage; never required (private windows may block it). */
export const local = {
  get(key: string): string | null {
    try { return window.localStorage.getItem(key); } catch { return null; }
  },
  set(key: string, value: string) {
    try { window.localStorage.setItem(key, value); } catch { /* ignore */ }
  },
};
