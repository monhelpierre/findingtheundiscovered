// Score classes for the map: five readable steps instead of a continuous gradient.
// The bounds follow the project's own thresholds (near / met / strong), so a colour always means
// the same thing on every map, in the criteria table and in the tiers.
// Colours: five steps of viridis, readable with every common colour-vision deficiency.

export type ScoreClass = { k: number; label: string; min: number; max: number; color: string; ink: string };

const COLORS = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"];
const INK = ["#fff", "#fff", "#fff", "#1d1d1b", "#1d1d1b"];
const LABELS = ["Very low", "Low", "Moderate", "High", "Very high"];

export function scoreClasses(t?: { near: number; met: number; strong: number }): ScoreClass[] {
  const b = [0, 0.25, t?.near ?? 0.5, t?.met ?? 0.7, t?.strong ?? 0.85, 1];
  return LABELS.map((label, k) => ({ k, label, min: b[k], max: b[k + 1], color: COLORS[k], ink: INK[k] }));
}

export const CLASSES = scoreClasses();

export function classOf(v: number, classes: ScoreClass[] = CLASSES): ScoreClass {
  for (let k = classes.length - 1; k >= 0; k--) if (v >= classes[k].min) return classes[k];
  return classes[0];
}

export function classColor(v: number, classes: ScoreClass[] = CLASSES): string {
  return classOf(v, classes).color;
}

/** "0.70–0.85" style range label. */
export function classRange(c: ScoreClass): string {
  return c.max >= 1 ? `≥ ${c.min.toFixed(2)}` : c.min <= 0 ? `< ${c.max.toFixed(2)}` : `${c.min.toFixed(2)}–${c.max.toFixed(2)}`;
}
