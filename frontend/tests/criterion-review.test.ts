import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { classOf, scoreClasses } from "../lib/classes";
import { confidence, mergeReviews, type Review } from "../lib/reviews";
import { criterionColumn, topCells } from "../lib/similarity";
import type { CriteriaDefs, Index, RegionData } from "../lib/types";

const data = path.resolve(__dirname, "..", "public", "data");
const index = JSON.parse(readFileSync(path.join(data, "index.json"), "utf-8")) as Index & CriteriaDefs;

describe("score classes", () => {
  const cls = scoreClasses(index.thresholds);
  it("follow the project thresholds", () => {
    expect(cls.map((c) => c.min)).toEqual([0, 0.25, index.thresholds.near, index.thresholds.met, index.thresholds.strong]);
  });
  it("put bounds in the upper class", () => {
    expect(classOf(0.7, cls).label).toBe("High");
    expect(classOf(0.6999, cls).label).toBe("Moderate");
    expect(classOf(1, cls).label).toBe("Very high");
    expect(classOf(0, cls).label).toBe("Very low");
  });
});

describe("one criterion alone", () => {
  // The exported candidates carry each criterion's score, computed by the pipeline (Python):
  // the browser's per-criterion column must give the same numbers.
  const checked: string[] = [];
  for (const [tid, cands] of Object.entries(index.candidates)) {
    const target = index.targets.find((t) => t.id === tid);
    if (!target) continue;
    for (const c of cands.slice(0, 3)) {
      if (!c.criteria) continue;
      const file = path.join(data, "regions", `${c.region}.json`);
      it(`matches the pipeline for ${tid} in ${c.region}`, () => {
        const region = JSON.parse(readFileSync(file, "utf-8")) as RegionData;
        const i = region.columns.cell.indexOf(c.cell);
        for (const [k, want] of Object.entries(c.criteria!)) {
          const got = criterionColumn(target, Number(k), region.columns, index)[i];
          if (want === null) expect(got).toBeNull();
          else expect(got).toBeCloseTo(want, 2);
        }
      });
      checked.push(tid);
      break;
    }
  }
  it("was checked against at least one exported candidate", () => expect(checked.length).toBeGreaterThan(0));

  it("ranks the best cells apart and outside the known-analog zones", () => {
    const region = JSON.parse(readFileSync(path.join(data, "regions", "atacama-yungay.json"), "utf-8")) as RegionData;
    const target = index.targets.find((t) => t.id === "mars_jezero")!;
    const v = criterionColumn(target, 2, region.columns, index);
    const top = topCells(v, region.columns, region.id, { exclusionKm: index.exclusion_km });
    expect(top.length).toBeGreaterThan(0);
    for (let k = 1; k < top.length; k++) expect(top[k].score).toBeLessThanOrEqual(top[k - 1].score);
    for (const c of top) {
      const i = region.columns.cell.indexOf(c.cell);
      expect(region.columns.analog_km[i] as number).toBeGreaterThanOrEqual(index.exclusion_km!);
    }
  });
});

describe("field review confidence", () => {
  const base: Review = {
    id: "a", region: "r", cell: 1, lat: 0, lon: 0, target_id: "t", method: "imagery", verdict: "",
    checks: {}, reliability: 0, author: "x", text: "", date: "2026-01-01",
  };
  it("is null without reviews", () => expect(confidence([])).toBeNull());
  it("is high only with matching checks and a site visit", () => {
    expect(confidence([{ ...base, checks: { "1": "ok", "2": "ok" } }])!.level).toBe("medium");
    expect(confidence([{ ...base, method: "field", checks: { "1": "ok", "2": "ok" } }])!.level).toBe("high");
  });
  it("weighs a site visit twice an imagery check", () => {
    const c = confidence([
      { ...base, id: "1", checks: { "2": "ok" } },
      { ...base, id: "2", method: "field", checks: { "2": "wrong" } },
    ])!;
    expect(c.criteria["2"]).toEqual({ ok: 1, wrong: 2 });
    expect(c.level).toBe("contested");
  });
  it("falls back on the reliability rating", () => {
    expect(confidence([{ ...base, reliability: 1 }])!.level).toBe("contested");
    expect(confidence([{ ...base, method: "field", reliability: 5 }])!.level).toBe("high");
  });
  it("does not count a shared review twice", () => {
    const merged = mergeReviews([base], [base, { ...base, id: "b" }]);
    expect(merged.map((r) => r.id)).toEqual(["a", "b"]);
  });
});

describe("site CSV export", () => {
  it("has the site, every criterion and every feature of the target", async () => {
    const { cellRows, toCsv } = await import("../lib/cellExport");
    const region = JSON.parse(readFileSync(path.join(data, "regions", "atacama-yungay.json"), "utf-8")) as RegionData;
    const target = index.targets.find((t) => t.id === "mars_jezero")!;
    const score = region.columns["score__mars_jezero"][0];
    const rows = cellRows({ region, i: 0, index, target, score, scoreLabel: "overall similarity",
      classes: scoreClasses(index.thresholds), conf: null });
    expect(rows.filter((r) => r.section === "criterion")).toHaveLength(index.applies.mars.length);
    expect(rows.filter((r) => r.section === "feature")).toHaveLength(Object.keys(target.features).length);
    const csv = toCsv(rows).split("\r\n");
    expect(csv[0]).toBe("section,item,value,unit,target,match,note");
    expect(csv.every((line) => !line.includes("undefined"))).toBe(true);
  });
  it("quotes values with commas or quotes", async () => {
    const { toCsv } = await import("../lib/cellExport");
    expect(toCsv([{ section: "s", item: 'a, "b"', value: "1" }]).split("\r\n")[1]).toBe('s,"a, ""b""",1,,,,');
  });
});

describe("Moon-base layer", () => {
  it("keeps only the chosen tiers, with each site's index", async () => {
    const { moonbasePoints } = await import("../components/Moonbase");
    const d = { name: "", method: "", n: 3, criteria: {},
      columns: { lat: [1, 2, 3], lon: [4, 5, 6], tier: ["Tier 2", "Tier 4", "Tier 3"] } };
    expect(moonbasePoints(d, new Set(["Tier 2", "Tier 3"]))).toEqual([
      { lat: 1, lon: 4, tier: "Tier 2", i: 0 }, { lat: 3, lon: 6, tier: "Tier 3", i: 2 },
    ]);
  });
});
