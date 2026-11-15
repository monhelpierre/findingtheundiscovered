// The browser recomputes per-criterion scores (lib/similarity.ts) for the cell details and radar
// chart. This checks they match what Python exported (backend/src/analog/criteria.py) for every
// candidate in public/data. Skipped when there is no exported data.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { criteriaScores, hasCriteria, tierOf } from "../lib/similarity";
import type { Index, RegionData } from "../lib/types";

const DATA = path.resolve(__dirname, "..", "public", "data");
const hasData = existsSync(path.join(DATA, "index.json"));
const read = <T,>(rel: string): T => JSON.parse(readFileSync(path.join(DATA, rel), "utf8")) as T;

describe.skipIf(!hasData)("browser criteria = Python export", () => {
  const index = hasData ? read<Index>("index.json") : ({} as Index);
  const regions = new Map<string, RegionData>();
  const region = (id: string) => {
    if (!regions.has(id)) regions.set(id, read<RegionData>(`regions/${id}.json`));
    return regions.get(id)!;
  };

  it("index has criteria definitions", () => {
    expect(hasCriteria(index)).toBe(true);
  });

  const cases = hasData
    ? index.targets.flatMap((t) => [
      ...(index.candidates[t.id] ?? []),
      ...(index.candidates_world?.[t.id] ?? []).slice(0, 5), // worldwide screen: its tile files are published
    ].map((c) => ({ t, c })))
    : [];

  it.each(cases.map(({ t, c }) => [`${t.id} @ ${c.region}#${c.cell}`, t, c] as const))(
    "%s",
    (_name, t, c) => {
      if (!hasCriteria(index)) throw new Error("no criteria definitions");
      const r = region(c.region);
      const i = (r.columns.cell as number[]).indexOf(c.cell);
      expect(i).toBeGreaterThanOrEqual(0);
      const overall = r.columns[`score__${t.id}`][i];
      const scores = criteriaScores(t, (n) => r.columns[n]?.[i], index, overall);
      for (const s of scores) {
        const py = c.criteria?.[String(s.k)] ?? null;
        if (py === null) expect(s.score).toBeNull();
        // Region files store features rounded to 3 decimals, hence the small tolerance.
        else expect(s.score!).toBeCloseTo(py, 2);
      }
      expect(tierOf(scores, index).tier).toBe(c.tier);
    },
  );
});
