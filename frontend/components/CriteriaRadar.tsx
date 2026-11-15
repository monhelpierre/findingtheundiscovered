"use client";

import type { CriterionScore } from "@/lib/similarity";

/** Radar chart of per-criterion scores (0-1). The dashed ring is the "met" threshold;
 *  criteria with no data are drawn at 0 with a hollow marker and a muted label. */
export default function CriteriaRadar({ scores, met }: { scores: CriterionScore[]; met: number }) {
  const n = scores.length;
  if (n < 3) return null;
  const size = 220, c = size / 2, R = 72;
  const pt = (i: number, r: number) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
    return [c + r * R * Math.cos(a), c + r * R * Math.sin(a)] as const;
  };
  const ring = (r: number) => scores.map((_, i) => pt(i, r).join(",")).join(" ");
  const poly = scores.map((s, i) => pt(i, s.score ?? 0).join(",")).join(" ");
  return (
    <svg viewBox={`-80 0 ${size + 160} ${size}`} className="radar" role="img"
      aria-label={scores.map((s) => `${s.short}: ${s.score === null ? "no data" : s.score.toFixed(2)}`).join("; ")}>
      {[0.25, 0.5, 0.75, 1].map((r) => <polygon key={r} points={ring(r)} className="radar-grid" />)}
      <polygon points={ring(met)} className="radar-met" />
      {scores.map((_, i) => {
        const [x, y] = pt(i, 1);
        return <line key={i} x1={c} y1={c} x2={x} y2={y} className="radar-grid" />;
      })}
      <polygon points={poly} className="radar-area" />
      {scores.map((s, i) => {
        const [x, y] = pt(i, s.score ?? 0);
        return <circle key={s.k} cx={x} cy={y} r={3} className={s.score === null ? "radar-dot nodata" : "radar-dot"} />;
      })}
      {scores.map((s, i) => {
        const [x, y] = pt(i, 1.2);
        const anchor = Math.abs(x - c) < 4 ? "middle" : x > c ? "start" : "end";
        return (
          <text key={s.k} x={x} y={y} textAnchor={anchor} dominantBaseline="middle"
            className={s.score === null ? "radar-label nodata" : "radar-label"}>
            {s.k}. {s.short}
          </text>
        );
      })}
    </svg>
  );
}
