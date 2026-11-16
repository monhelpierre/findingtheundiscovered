"use client";

// Moon-base sites of the sister project (Artemis III profile, whole Earth at 10 km): an extra map layer
// with its own panel. Data: /data/moonbase.json, written by `analog moonbase <sites.parquet>`.
import { useMemo } from "react";

export type MoonbaseData = {
  name: string; method: string; n: number;
  criteria: Record<string, string>;
  columns: Record<string, (string | number | boolean | null)[]>;
};

export const MB_TIERS = ["Tier 1", "Tier 2", "Tier 3", "Tier 4"] as const;
const TIER_CLASS: Record<string, string> = { "Tier 1": "A", "Tier 2": "B", "Tier 3": "C", "Tier 4": "D" };

export async function loadMoonbase(): Promise<MoonbaseData | null> {
  try {
    const r = await fetch("/data/moonbase.json", { cache: "no-store" });
    if (!r.ok || !(r.headers.get("content-type") ?? "").includes("json")) return null;
    return (await r.json()) as MoonbaseData;
  } catch {
    return null;
  }
}

export function moonbasePoints(d: MoonbaseData, tiers: Set<string>) {
  const c = d.columns;
  const out: { lat: number; lon: number; tier: string; i: number }[] = [];
  for (let i = 0; i < d.n; i++) {
    const tier = String(c.tier[i]);
    if (tiers.has(tier)) out.push({ lat: c.lat[i] as number, lon: c.lon[i] as number, tier, i });
  }
  return out;
}

const get = (d: MoonbaseData, k: string, i: number) => d.columns[k]?.[i] ?? null;

export default function MoonbaseCard({ on, setOn, data, tiers, setTiers, selected, onPick }: {
  on: boolean; setOn: (b: boolean) => void; data: MoonbaseData | null;
  tiers: Set<string>; setTiers: (t: Set<string>) => void;
  selected: number | null; onPick: (i: number) => void;
}) {
  const counts = useMemo(() => {
    const n: Record<string, number> = {};
    for (const t of data?.columns.tier ?? []) n[String(t)] = (n[String(t)] ?? 0) + 1;
    return n;
  }, [data]);
  // Best site of each continent: the lowest overall rank among its "best on continent" sites.
  const best = useMemo(() => {
    if (!data) return [];
    const by = new Map<string, number>();
    for (let i = 0; i < data.n; i++) {
      const cont = String(get(data, "continent", i) ?? "?");
      const prev = by.get(cont);
      if (prev === undefined || (get(data, "rank", i) as number) < (get(data, "rank", prev) as number)) by.set(cont, i);
    }
    return [...by.values()].sort((a, b) => (get(data, "S", b) as number) - (get(data, "S", a) as number));
  }, [data]);

  return (
    <section className="card moonbase">
      <label className="check">
        <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
        <span>
          <strong>Moon-base sites, whole Earth</strong>
          <span className="muted small"> · Artemis III profile of the sister project: every 10 km square of land,
            grouped into sites of at most 50 km</span>
        </span>
      </label>
      {on && !data && <p className="muted small">Loading…</p>}
      {on && data && (
        <>
          <div className="seg small" role="group" aria-label="Tiers shown">
            {MB_TIERS.filter((t) => counts[t]).map((t) => (
              <button key={t} type="button" aria-pressed={tiers.has(t)} className={tiers.has(t) ? "on" : ""}
                onClick={() => {
                  const next = new Set(tiers);
                  if (next.has(t)) next.delete(t); else next.add(t);
                  setTiers(next);
                }}>
                <span className={`tier tier-${TIER_CLASS[t]}`}>{t.slice(5)}</span>{counts[t].toLocaleString()}
              </button>
            ))}
          </div>
          <p className="muted small">
            Tier = share of the Artemis criteria met, as in our tiers (1 = A … 4 = D). Their score weighs illumination
            most (0.47), so the best sites are near the Arctic Circle. <details className="inline"><summary>Method</summary>{data.method}</details>
          </p>
          {selected !== null && <MoonbaseDetail data={data} i={selected} />}
          <h3 className="small">Best site on each continent</h3>
          <ol className="cands">
            {best.map((i) => (
              <li key={i}>
                <button onClick={() => onPick(i)} className={selected === i ? "picked" : ""}>
                  <span className={`tier tier-${TIER_CLASS[String(get(data, "tier", i))]}`}>{String(get(data, "tier", i)).slice(5)}</span>
                  <span className="grow">
                    {String(get(data, "site_code", i))}
                    <span className="muted small"> · {String(get(data, "country", i) ?? "")}, {String(get(data, "continent", i))}</span>
                  </span>
                  <span className="score">{(get(data, "S", i) as number).toFixed(2)}</span>
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function MoonbaseDetail({ data, i }: { data: MoonbaseData; i: number }) {
  const lat = get(data, "lat", i) as number, lon = get(data, "lon", i) as number;
  const crit = Object.entries(data.criteria);
  return (
    <div className="conf-card conf-medium mb-detail">
      <strong>{String(get(data, "site_code", i))} · {String(get(data, "country", i) ?? "")}</strong>
      <div className="small">
        {String(get(data, "tier", i))}, score {(get(data, "S", i) as number).toFixed(2)},{" "}
        {String(get(data, "n_met", i))} of {String(get(data, "n_scored", i))} criteria met · {lat.toFixed(3)}, {lon.toFixed(3)} ·{" "}
        <a href={`https://www.google.com/maps/@${lat},${lon},11z/data=!3m1!1e3`} target="_blank" rel="noreferrer">open in Maps</a>
      </div>
      <table className="profile">
        <tbody>
          {crit.map(([k, label]) => {
            const v = get(data, k, i) as number | null;
            return (
              <tr key={k}>
                <td>{label}</td>
                <td>
                  {v === null ? <span className="muted small">not scored</span> : (
                    <span className="match"><span className="bar" style={{ width: `${Math.round(v * 100)}%` }} /><span className="small">{v.toFixed(2)}</span></span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="small muted">
        Low-sun days: {String(get(data, "low_sun_days", i) ?? "–")} a year · ice: {String(get(data, "ice_label", i) ?? "–")} ·
        area {String(get(data, "area_km2", i) ?? "–")} km²
      </div>
    </div>
  );
}
