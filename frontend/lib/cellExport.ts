// Export what the "Selected cell" card shows (site, criteria, features, field reviews) as a CSV file.
import { classOf, type ScoreClass } from "./classes";
import type { Confidence } from "./reviews";
import {
  band, columnOf, criteriaScores, featureSimilarity, hasCriteria, humanize, modeLabel, normCdf, tierOf,
} from "./similarity";
import type { Index, RegionData, Target } from "./types";

type Row = { section: string; item: string; value: string; unit?: string; target?: string; match?: string; note?: string };

const HEADER = ["section", "item", "value", "unit", "target", "match", "note"] as const;

function cell(v: string | undefined): string {
  const s = v ?? "";
  return /[",\n\r;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

const num = (x: number | null | undefined, d = 3) =>
  x === null || x === undefined || Number.isNaN(x) ? "" : String(Math.round(x * 10 ** d) / 10 ** d);

export function cellRows(opts: {
  region: RegionData; i: number; index: Index; target: Target | null; score: number | null | undefined;
  scoreLabel: string; classes: ScoreClass[]; conf: Confidence | null; regionTitle?: string;
}): Row[] {
  const { region, i, index, target, score, scoreLabel, classes, conf } = opts;
  const c = region.columns;
  const rows: Row[] = [];
  const site = (item: string, value: string, note?: string) => rows.push({ section: "site", item, value, note });

  site("latitude", num(c.lat[i] as number, 5));
  site("longitude", num(c.lon[i] as number, 5));
  site("region", region.id, opts.regionTitle);
  site("cell", String(c.cell[i]));
  site("cell size (deg)", String(region.cell_deg));
  if (target) site("target", target.id, `${target.name} (${target.body}${target.mission ? `, ${target.mission}` : ""})`);
  site("score", num(score), scoreLabel);
  if (typeof score === "number") {
    const k = classOf(score, classes);
    site("class", k.label, `${num(k.min, 2)} to ${num(k.max, 2)}`);
  }
  const near = c.analog_km?.[i];
  if (typeof near === "number") {
    site("distance to nearest known analog (km)", num(near, 1),
      index.exclusion_km !== undefined && near < index.exclusion_km ? "inside the exclusion zone: never a candidate" : undefined);
  }
  site("imagery", `https://www.google.com/maps/@${c.lat[i]},${c.lon[i]},14z/data=!3m1!1e3`);
  site("data exported", index.generated);

  if (target && hasCriteria(index)) {
    const scores = criteriaScores(target, (n) => c[n]?.[i], index, score);
    const { tier, met, scored } = tierOf(scores, index);
    site("tier", tier ?? "none", `${met} of ${scored} measured criteria met`);
    for (const s of scores) {
      rows.push({
        section: "criterion", item: `${s.k}. ${s.short}`, value: num(s.score), match: band(s.score, index),
        note: s.features.map((n) => index.features[n]?.label ?? n).join(" + ") || "no feature yet",
      });
    }
  }

  const keys = target ? Object.keys(target.features) : Object.keys(index.features).filter((k) => k in c);
  for (const k of keys) {
    const spec = target?.features[k];
    const cmp = spec ? c[columnOf(k, spec)]?.[i] : undefined;
    const s = spec ? featureSimilarity(cmp, spec) : null;
    rows.push({
      section: "feature", item: index.features[k]?.label ?? humanize(k), value: num(c[k]?.[i] as number | null, 4),
      unit: index.features[k]?.unit ?? "",
      target: spec ? modeLabel(spec) : undefined,
      match: s === null ? (spec ? "no data" : undefined) : `${Math.round(s * 100)}%`,
      note: spec?.column && typeof cmp === "number" ? `${Math.round(normCdf(cmp) * 100)}th percentile on Earth` : undefined,
    });
  }

  if (conf) {
    const r = (item: string, value: string, note?: string) => rows.push({ section: "field reviews", item, value, note });
    r("confidence", conf.level, `${conf.n} review(s), ${conf.field} on site`);
    if (conf.agreement !== null) r("data matched (share of checks)", num(conf.agreement, 2));
    if (conf.reliability !== null) r("reliability (1-5)", num(conf.reliability, 1));
    for (const [k, v] of Object.entries(conf.criteria)) {
      r(`criterion ${k}: ${index.criteria?.[k]?.short ?? ""}`.trim(), `matches ${v.ok}, wrong ${v.wrong}`, "site visits count twice");
    }
  }
  return rows;
}

export function toCsv(rows: Row[]): string {
  return [HEADER.join(","), ...rows.map((r) => HEADER.map((h) => cell(r[h])).join(","))].join("\r\n");
}

export function downloadCsv(name: string, csv: string) {
  // BOM so Excel reads the accents and degree signs as UTF-8.
  const blob = new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
