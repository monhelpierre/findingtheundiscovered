import type { CriteriaDefs, FeatureSpec, Target, Tier, View } from "./types";

/** Same formula as src/analog/similarity.py (per-feature part). */
export function featureZ(x: number, s: FeatureSpec): number {
  const mode = s.mode ?? "near";
  if (mode === "atleast") return Math.max(0, (s.value - x) / s.sigma);
  if (mode === "atmost") return Math.max(0, (x - s.value) / s.sigma);
  return (x - s.value) / s.sigma;
}

export function featureSimilarity(x: number | null | undefined, s: FeatureSpec): number | null {
  if (x === null || x === undefined || Number.isNaN(x)) return null;
  const z = featureZ(x, s);
  return Math.exp(-0.5 * z * z);
}

export function valueColumn(v: View): string {
  if (v.kind === "target") return `score__${v.id}`;
  if (v.kind === "classifier") return "analog_prob";
  return `prob__${v.cls}`;
}

export function viewKey(v: View): string {
  if (v.kind === "target") return `t:${v.id}`;
  if (v.kind === "classifier") return "classifier";
  return `c:${v.cls}`;
}

export function parseViewKey(k: string): View {
  if (k === "classifier") return { kind: "classifier" };
  if (k.startsWith("c:")) return { kind: "class", cls: k.slice(2) };
  return { kind: "target", id: k.slice(2) };
}

export function fmt(x: number | null | undefined, unit = ""): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  const a = Math.abs(x);
  const s = a >= 100 ? x.toFixed(0) : a >= 10 ? x.toFixed(1) : a >= 1 ? x.toFixed(2) : x.toFixed(3);
  return unit && unit !== "fraction" && unit !== "0-1" && unit !== "-1..1" ? `${s} ${unit}` : s;
}

/** Standard normal CDF (Abramowitz & Stegun 7.1.26, error < 1.5e-7): Earth rank from a rel__ value. */
export function normCdf(z: number): number {
  const t = 1 / (1 + 0.3275911 * (Math.abs(z) / Math.SQRT2));
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t
    * Math.exp(-(z * z) / 2);
  return z >= 0 ? (1 + y) / 2 : (1 - y) / 2;
}

/** The column holding the value a spec is compared with (rel__<name> for percentile features). */
export const columnOf = (name: string, s: FeatureSpec) => s.column ?? name;

/** "p5" for a 0-1 rank; one decimal near the ends ("p0.3", "p99.7"). */
export const pct = (r: number) => {
  const v = r * 100;
  return `p${v < 1 || v > 99 ? (Math.round(v * 10) / 10).toString() : Math.round(v)}`;
};

export function modeLabel(s: FeatureSpec): string {
  const m = s.mode ?? "near";
  if (s.percentile !== undefined && s.percentile !== null) {
    const p = `${pct(s.percentile)} of its world`;
    return m === "atleast" ? `≥ ${p}` : m === "atmost" ? `≤ ${p}` : p;
  }
  if (m === "atleast") return `≥ ${s.value}`;
  if (m === "atmost") return `≤ ${s.value}`;
  return `${s.value} ± ${s.sigma}`;
}

export const humanize = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

// ---- The brief's 8 criteria (same logic as src/analog/criteria.py) -----------------------------


export type CriterionScore = { k: number; short: string; label: string; score: number | null; features: string[] };

export function criterionOf(name: string, spec: FeatureSpec, d: CriteriaDefs): number | null {
  if (spec.criterion !== undefined && spec.criterion !== null) return spec.criterion;
  if (d.gates.includes(name)) return null;
  if (name in d.feature_criterion) return d.feature_criterion[name];
  return name.startsWith("min_") ? 1 : null;
}

/** Per-criterion scores for one cell. `value(name)` returns the cell's feature value. */
export function criteriaScores(
  target: Target, value: (name: string) => number | null | undefined, d: CriteriaDefs, overall: number | null | undefined,
): CriterionScore[] {
  const applies = d.applies[target.body] ?? d.applies.earth;
  let gate = overall !== null && overall !== undefined && !Number.isNaN(overall);
  for (const [name, spec] of Object.entries(target.features)) {
    if (d.gates.includes(name) && criterionOf(name, spec, d) === null) {
      const s = featureSimilarity(value(columnOf(name, spec)), spec);
      if (s === null || s < d.thresholds.met) gate = false;
    }
  }
  return applies.map((k) => {
    const names = Object.entries(target.features).filter(([n, s]) => criterionOf(n, s, d) === k).map(([n]) => n);
    let num = 0, wsum = 0;
    for (const n of names) {
      const spec = target.features[n];
      const x = value(columnOf(n, spec));
      if (x === null || x === undefined || Number.isNaN(x)) continue;
      const w = spec.weight ?? 1;
      const z = featureZ(x, spec);
      num += w * z * z;
      wsum += w;
    }
    const score = gate && wsum > 0 ? Math.exp((-0.5 * num) / wsum) : null;
    const c = d.criteria[String(k)];
    return { k, short: c?.short ?? `C${k}`, label: c?.label ?? "", score, features: names };
  });
}

export function band(score: number | null, d: CriteriaDefs): "strong" | "match" | "near miss" | "mismatch" | "no data" {
  if (score === null) return "no data";
  const t = d.thresholds;
  return score >= t.strong ? "strong" : score >= t.met ? "match" : score >= t.near ? "near miss" : "mismatch";
}

export function tierOf(scores: CriterionScore[], d: CriteriaDefs): { tier: Tier | null; met: number; scored: number } {
  const scored = scores.filter((s) => s.score !== null).length;
  const met = scores.filter((s) => s.score !== null && s.score >= d.thresholds.met).length;
  if (!scored || !met) return { tier: null, met, scored };
  const ratio = met / scored;
  return { tier: d.tiers.find((t) => ratio >= t.min_ratio)?.tier ?? null, met, scored };
}

export function hasCriteria(index: Partial<CriteriaDefs>): index is CriteriaDefs {
  return !!(index.criteria && index.applies && index.feature_criterion && index.thresholds && index.tiers && index.gates);
}

/** Features of `target` that make up criterion k. */
export function criterionFeatures(target: Target, k: number, d: CriteriaDefs): string[] {
  return Object.entries(target.features).filter(([n, s]) => criterionOf(n, s, d) === k).map(([n]) => n);
}

/** Score of one criterion alone for every cell of a region (same formula as the criteria table:
 *  the target's features for that criterion only, gates such as vegetation still apply). */
export function criterionColumn(
  target: Target, k: number, columns: Record<string, (number | null)[]>, d: CriteriaDefs,
): (number | null)[] {
  const names = criterionFeatures(target, k, d);
  const gates = Object.entries(target.features).filter(([n, s]) => d.gates.includes(n) && criterionOf(n, s, d) === null);
  const n = columns.cell?.length ?? 0;
  const out: (number | null)[] = new Array(n);
  for (let i = 0; i < n; i++) {
    let open = true;
    for (const [g, spec] of gates) {
      const s = featureSimilarity(columns[columnOf(g, spec)]?.[i], spec);
      if (s === null || s < d.thresholds.met) { open = false; break; }
    }
    let num = 0, wsum = 0;
    if (open) {
      for (const name of names) {
        const spec = target.features[name];
        const x = columns[columnOf(name, spec)]?.[i];
        if (x === null || x === undefined || Number.isNaN(x)) continue;
        const w = spec.weight ?? 1;
        const z = featureZ(x, spec);
        num += w * z * z;
        wsum += w;
      }
    }
    out[i] = open && wsum > 0 ? Math.exp((-0.5 * num) / wsum) : null;
  }
  return out;
}

function km(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * r) / 2) ** 2
    + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(a));
}

/** Best cells of one region for a score column, like `analog export` does: outside the known-analog
 *  zones, above min_score, at least min_km apart. */
export function topCells(
  values: (number | null)[], columns: Record<string, (number | null)[]>, region: string,
  { n = 10, minKm = 15, minScore = 0.2, exclusionKm }: { n?: number; minKm?: number; minScore?: number; exclusionKm?: number } = {},
): { lat: number; lon: number; score: number; cell: number; region: string }[] {
  const order = values.map((v, i) => [v, i] as const)
    .filter((p): p is readonly [number, number] => typeof p[0] === "number" && p[0] >= minScore)
    .sort((a, b) => b[0] - a[0]);
  const out: { lat: number; lon: number; score: number; cell: number; region: string }[] = [];
  for (const [score, i] of order) {
    const near = columns.analog_km?.[i];
    if (exclusionKm !== undefined && typeof near === "number" && near < exclusionKm) continue;
    const lat = columns.lat[i] as number, lon = columns.lon[i] as number;
    if (out.some((c) => km(c.lat, c.lon, lat, lon) < minKm)) continue;
    out.push({ lat, lon, score: Math.round(score * 1000) / 1000, cell: columns.cell[i] as number, region });
    if (out.length >= n) break;
  }
  return out;
}
