import { describe, expect, it } from "vitest";
import { band, criteriaScores, featureSimilarity, featureZ, modeLabel, normCdf, tierOf } from "../lib/similarity";
import type { CriteriaDefs, Target } from "../lib/types";

// Same definitions as backend/src/analog/criteria.py (describe()).
const DEFS: CriteriaDefs = {
  criteria: {
    "1": { short: "Geology", label: "" }, "2": { short: "Terrain", label: "" }, "3": { short: "Thermal", label: "" },
    "4": { short: "Water & ice", label: "" }, "5": { short: "Surface processes", label: "" },
    "6": { short: "Biology", label: "" }, "7": { short: "Subsurface", label: "" }, "8": { short: "Operations", label: "" },
  },
  applies: { moon: [1, 2, 3, 4, 8], mars: [1, 2, 3, 4, 5, 8], earth: [1, 2, 3, 4, 5, 8] },
  feature_criterion: { albedo: 1, clay_index: 1, ferric_index: 1, slope_mean: 2, slope_p75: 2, relief: 2,
    roughness: 2, temp_range: 3, water_fraction: 4, gentle_slope_frac: 8 },
  gates: ["ndvi"],
  thresholds: { strong: 0.85, met: 0.7, near: 0.5 },
  tiers: [
    { min_ratio: 0.75, tier: "A", label: "Strong match" }, { min_ratio: 0.5, tier: "B", label: "Good analog" },
    { min_ratio: 0.25, tier: "C", label: "Partial analog" }, { min_ratio: 0, tier: "D", label: "Single-criterion analog" },
  ],
};

const TARGET: Target = {
  id: "moon_flat", name: "Flat plain", body: "moon",
  features: {
    slope_mean: { value: 5, sigma: 3 },
    relief: { value: 40, sigma: 30 },
    albedo: { value: 0.25, sigma: 0.08 },
    min_mafic: { value: 0.2, sigma: 0.1 },
    gentle_slope_frac: { value: 0.95, sigma: 0.1, mode: "atleast" },
    ndvi: { value: 0.1, sigma: 0.06, mode: "atmost", weight: 1.5 },
  },
};

const cell = (v: Record<string, number | null>) => (name: string) => v[name];

describe("featureZ / featureSimilarity", () => {
  it("near, atleast and atmost modes", () => {
    expect(featureZ(7, { value: 5, sigma: 2 })).toBe(1);
    expect(featureZ(3, { value: 5, sigma: 2, mode: "atleast" })).toBe(1);
    expect(featureZ(9, { value: 5, sigma: 2, mode: "atleast" })).toBe(0);
    expect(featureZ(9, { value: 5, sigma: 2, mode: "atmost" })).toBe(2);
    expect(featureZ(1, { value: 5, sigma: 2, mode: "atmost" })).toBe(0);
  });
  it("one sigma off scores exp(-1/2); missing data scores null", () => {
    expect(featureSimilarity(7, { value: 5, sigma: 2 })).toBeCloseTo(Math.exp(-0.5), 10);
    expect(featureSimilarity(null, { value: 5, sigma: 2 })).toBeNull();
    expect(featureSimilarity(Number.NaN, { value: 5, sigma: 2 })).toBeNull();
  });
});

describe("criteria and tiers", () => {
  it("groups features into criteria, in the order that applies to the body", () => {
    const s = criteriaScores(TARGET, cell({ slope_mean: 5, relief: 40, albedo: 0.25, min_mafic: 0.2, gentle_slope_frac: 1, ndvi: 0.05 }), DEFS, 1);
    expect(s.map((c) => c.k)).toEqual([1, 2, 3, 4, 8]);
    expect(s.find((c) => c.k === 1)!.features.sort()).toEqual(["albedo", "min_mafic"]);
    expect(s.find((c) => c.k === 2)!.features.sort()).toEqual(["relief", "slope_mean"]); // slope = terrain
    expect(s.find((c) => c.k === 8)!.features).toEqual(["gentle_slope_frac"]);             // 0-15 deg ground
    expect(s.find((c) => c.k === 3)!.score).toBeNull(); // no thermal feature in this target
  });

  it("a perfect cell is tier A on the criteria that have data", () => {
    const s = criteriaScores(TARGET, cell({ slope_mean: 5, relief: 40, albedo: 0.25, min_mafic: 0.2, gentle_slope_frac: 1, ndvi: 0.05 }), DEFS, 1);
    expect(tierOf(s, DEFS)).toEqual({ tier: "A", met: 3, scored: 3 });
  });

  it("vegetated ground fails the NDVI gate: no criteria, no tier", () => {
    const s = criteriaScores(TARGET, cell({ slope_mean: 5, relief: 40, albedo: 0.25, min_mafic: 0.2, gentle_slope_frac: 1, ndvi: 0.6 }), DEFS, 0.4);
    expect(s.every((c) => c.score === null)).toBe(true);
    expect(tierOf(s, DEFS).tier).toBeNull();
  });

  it("no overall score (e.g. water) means no criteria either", () => {
    const s = criteriaScores(TARGET, cell({ slope_mean: 5, relief: 40, albedo: 0.25, min_mafic: 0.2, gentle_slope_frac: 1, ndvi: 0.05 }), DEFS, null);
    expect(s.every((c) => c.score === null)).toBe(true);
  });

  it("1 of 3 criteria met is tier C; steep ground fails terrain and operations", () => {
    const s = criteriaScores(TARGET, cell({ slope_mean: 25, relief: 300, albedo: 0.25, min_mafic: 0.2, gentle_slope_frac: 0.4, ndvi: 0.05 }), DEFS, 0.3);
    expect(s.find((c) => c.k === 2)!.score!).toBeLessThan(0.7);
    expect(s.find((c) => c.k === 8)!.score!).toBeLessThan(0.7);
    expect(tierOf(s, DEFS)).toEqual({ tier: "C", met: 1, scored: 3 });
  });

  it("bands follow the brief's thresholds", () => {
    expect(band(0.9, DEFS)).toBe("strong");
    expect(band(0.7, DEFS)).toBe("match");
    expect(band(0.6, DEFS)).toBe("near miss");
    expect(band(0.2, DEFS)).toBe("mismatch");
    expect(band(null, DEFS)).toBe("no data");
  });
});

describe("percentile features", () => {
  // As exported by Python (earthref.resolve_spec): value = Phi^-1(0.05), compared with column rel__tmin_k.
  const cold: Target = {
    id: "moon_cold", name: "Cold", body: "moon",
    features: { tmin_k: { percentile: 0.05, value: -1.644854, sigma: 1, column: "rel__tmin_k" } },
  };
  const defs = { ...DEFS, feature_criterion: { ...DEFS.feature_criterion, tmin_k: 3 } };

  it("normCdf inverts Phi^-1", () => {
    expect(normCdf(-1.644854)).toBeCloseTo(0.05, 5);
    expect(normCdf(0)).toBeCloseTo(0.5, 7);
    expect(normCdf(1.959964)).toBeCloseTo(0.975, 5);
  });

  it("scores the rel__ column, not the raw value", () => {
    const cell: Record<string, number> = { tmin_k: 250, rel__tmin_k: -1.644854 };
    const thermal = criteriaScores(cold, (n) => cell[n], defs, 1).find((c) => c.k === 3)!;
    expect(thermal.score).toBeCloseTo(1, 6);
    const median: Record<string, number> = { tmin_k: 280, rel__tmin_k: 0 };
    const t2 = criteriaScores(cold, (n) => median[n], defs, 1).find((c) => c.k === 3)!;
    expect(t2.score).toBeCloseTo(Math.exp(-0.5 * 1.644854 ** 2), 6);
  });

  it("labels the target by percentile", () => {
    expect(modeLabel(cold.features.tmin_k)).toBe("p5 of its world");
    expect(modeLabel({ ...cold.features.tmin_k, mode: "atmost" })).toBe("≤ p5 of its world");
  });
});
