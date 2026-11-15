// Field reviews: people who checked a site (on the ground or on imagery) say whether the data and
// the scores shown match what they saw. No backend needed:
// - your own reviews are kept in this browser (localStorage);
// - the team's shared reviews are a static file, public/reviews.json. To share yours, download
//   them ("Download my reviews") and add them to that file; everyone then sees them.
import { local } from "./api";

export type Check = "ok" | "wrong";          // the data for this criterion matches / doesn't match
export type Verdict = "good" | "bad" | "unsure";

export type Review = {
  id: string;
  region: string; cell: number; lat: number; lon: number;
  target_id: string;
  /** "field": visited on site (counts double); "imagery": checked on satellite imagery */
  method: "field" | "imagery";
  verdict: Verdict | "";
  /** Per criterion ("1".."8"): does the satellite data match the observation? Unchecked criteria are absent. */
  checks: Record<string, Check>;
  /** Overall reliability of the data and scores shown, 1 (wrong) to 5 (exact); 0 = not rated */
  reliability: number;
  author: string;
  text: string;
  date: string;
};

const KEY = "analog.reviews";

export function cellKey(region: string, cell: number) {
  return `${region}:${cell}`;
}

export function loadLocal(): Review[] {
  try {
    const v = JSON.parse(local.get(KEY) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

export function saveLocal(reviews: Review[]) {
  local.set(KEY, JSON.stringify(reviews));
}

export async function loadShared(): Promise<Review[]> {
  try {
    const r = await fetch("/reviews.json", { cache: "no-store" });
    if (!r.ok) return [];
    const v = await r.json();
    return Array.isArray(v) ? v : Array.isArray(v?.reviews) ? v.reviews : [];
  } catch {
    return [];
  }
}

/** Shared + local, without duplicates (a downloaded review merged into the file keeps its id). */
export function mergeReviews(shared: Review[], mine: Review[]): (Review & { mine?: boolean })[] {
  const ids = new Set(shared.map((r) => r.id));
  return [...shared, ...mine.filter((r) => !ids.has(r.id)).map((r) => ({ ...r, mine: true }))];
}

export function newId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export type Confidence = {
  n: number; field: number;
  verdicts: Record<Verdict, number>;
  /** Weighted share of criterion checks that matched (null: nobody checked a criterion) */
  agreement: number | null;
  /** Mean reliability 1-5 (null: not rated) */
  reliability: number | null;
  /** Per criterion: weighted matched / wrong counts */
  criteria: Record<string, { ok: number; wrong: number }>;
  level: "high" | "medium" | "low" | "contested";
};

/** Combine the reviews of one cell into a confidence indicator. Field visits weigh twice an imagery check. */
export function confidence(reviews: Review[]): Confidence | null {
  if (!reviews.length) return null;
  const verdicts: Record<Verdict, number> = { good: 0, bad: 0, unsure: 0 };
  const criteria: Confidence["criteria"] = {};
  let ok = 0, wrong = 0, rel = 0, relW = 0, field = 0;
  for (const r of reviews) {
    const w = r.method === "field" ? 2 : 1;
    if (r.method === "field") field++;
    if (r.verdict) verdicts[r.verdict]++;
    if (r.reliability > 0) { rel += w * r.reliability; relW += w; }
    for (const [k, c] of Object.entries(r.checks ?? {})) {
      const e = (criteria[k] ??= { ok: 0, wrong: 0 });
      if (c === "ok") { e.ok += w; ok += w; } else { e.wrong += w; wrong += w; }
    }
  }
  const agreement = ok + wrong ? ok / (ok + wrong) : null;
  const reliability = relW ? rel / relW : null;
  // One number to rank on: criterion agreement if any, else the reliability rating.
  const s = agreement ?? (reliability !== null ? (reliability - 1) / 4 : null);
  const level: Confidence["level"] =
    s === null ? "low"
      : s < 0.5 ? "contested"
        : s >= 0.75 && field > 0 ? "high"
          : "medium";
  return { n: reviews.length, field, verdicts, agreement, reliability, criteria, level };
}

export const LEVEL_LABEL: Record<Confidence["level"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
  contested: "Data contested",
};

export function download(reviews: Review[]) {
  const clean = reviews.map(({ mine: _m, ...r }: Review & { mine?: boolean }) => r);
  const blob = new Blob([JSON.stringify(clean, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `reviews-${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
