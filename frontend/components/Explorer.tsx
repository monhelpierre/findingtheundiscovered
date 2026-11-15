"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import MapView, { RAMP } from "./MapView";
import CriteriaRadar from "./CriteriaRadar";
import { AppSplash, MapLoading } from "./Loader";
import { CREDITS } from "@/lib/credits";
import type { Candidate, CriteriaDefs, Index, RegionData, Target, View } from "@/lib/types";
import { addNote, getNotes, load, local, scoreRegion, type Note, type ScoreResult, type Source } from "@/lib/api";
import {
  band, columnOf, criteriaScores, featureSimilarity, fmt, hasCriteria, humanize, modeLabel, normCdf, parseViewKey, pct, tierOf,
  valueColumn, viewKey,
} from "@/lib/similarity";

const BODY_LABEL: Record<string, string> = { moon: "Moon", mars: "Mars", earth: "Earth" };

export default function Explorer() {
  const [index, setIndex] = useState<Index | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [regionId, setRegionId] = useState<string | null>(null);
  const [region, setRegion] = useState<RegionData | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const [focus, setFocus] = useState<{ lon: number; lat: number; key: number } | null>(null);
  const [pendingCell, setPendingCell] = useState<{ region: string; cell: number } | null>(null);
  const [opacity, setOpacity] = useState(0.9);
  const [loading, setLoading] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  // Never leave the map covered: browsers pause rendering in hidden tabs, which delays MapLibre's "load".
  useEffect(() => {
    const t = setTimeout(() => setMapReady(true), 10_000);
    return () => clearTimeout(t);
  }, []);
  const [source, setSource] = useState<Source | null>(null);
  // Custom feature weights for the current target (backend only), and the re-scored region.
  const [weights, setWeights] = useState<Record<string, number> | null>(null);
  const [custom, setCustom] = useState<ScoreResult | null>(null);
  const [scoring, setScoring] = useState(false);
  // Worldwide screen (Earth Engine tiles) instead of the team's study regions
  const [worldwide, setWorldwide] = useState(false);
  // Side panel width, dragged with the handle between panel and map (remembered in this browser)
  const [panelW, setPanelW] = useState(380);
  useEffect(() => {
    const w = Number(local.get("analog.panelWidth"));
    if (w >= 300 && w <= 900) setPanelW(w);
  }, []);
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: PointerEvent) => setPanelW(Math.min(900, Math.max(300, ev.clientX)));
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      local.set("analog.panelWidth", String(Math.min(900, Math.max(300, ev.clientX))));
      document.body.classList.remove("resizing");
    };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const loadIndex = useCallback(async () => {
    setError(null);
    // The backend's /api/index if it's running, else the static copy made by `analog export`.
    let idx: Index;
    try {
      const got = await load<Index>("/api/index", "/data/index.json");
      idx = got.data;
      setSource(got.source);
    } catch {
      setError("No exported data: start the backend, or run `analog export` (or `npm run sync-data` in frontend/).");
      return;
    }
    setIndex(idx);
    setView((v) => v ?? (idx.targets[0] ? { kind: "target", id: idx.targets[0].id } : null));
    setRegionId((id) => (id && idx.regions.some((r) => r.id === id) ? id : idx.regions[0]?.id ?? null));
    return idx;
  }, []);

  const loadRegion = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const rid = encodeURIComponent(id);
      setRegion((await load<RegionData>(`/api/regions/${rid}`, `/data/regions/${rid}.json`)).data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadIndex();
  }, [loadIndex]);

  useEffect(() => {
    setSelected(null);
    setHover(null);
    if (regionId) loadRegion(regionId);
  }, [regionId, loadRegion]);

  // After switching region for a candidate, select its cell once the data arrives.
  useEffect(() => {
    if (!region || !pendingCell || pendingCell.region !== region.id) return;
    const i = region.columns.cell.indexOf(pendingCell.cell);
    if (i >= 0) setSelected(i);
    setPendingCell(null);
  }, [region, pendingCell]);

  const reload = async () => {
    await loadIndex();
    if (regionId) await loadRegion(regionId);
  };

  const baseTarget: Target | null =
    view?.kind === "target" ? index?.targets.find((t) => t.id === view.id) ?? null : null;
  // The target as currently weighted (sliders), used for the details table and criteria.
  const target: Target | null = useMemo(() => {
    if (!baseTarget || !weights) return baseTarget;
    const features = Object.fromEntries(Object.entries(baseTarget.features).map(
      ([k, s]) => [k, k in weights ? { ...s, weight: weights[k] } : s]));
    return { ...baseTarget, features };
  }, [baseTarget, weights]);

  // New target: back to its own weights.
  useEffect(() => { setWeights(null); setCustom(null); }, [baseTarget?.id]);

  // Re-score the current region on the backend when the weights change (debounced).
  useEffect(() => {
    if (!weights || !baseTarget || !regionId) { setCustom(null); return; }
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      setScoring(true);
      try {
        setCustom(await scoreRegion(baseTarget.id, regionId, weights, ctl.signal));
        setError(null);
      } catch (e) {
        if (!ctl.signal.aborted) setError(`Re-scoring failed: ${e instanceof Error ? e.message : e}`);
      } finally {
        if (!ctl.signal.aborted) setScoring(false);
      }
    }, 300);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [weights, baseTarget, regionId]);

  const customActive = !!(custom && weights && custom.region === region?.id && custom.target === baseTarget?.id);
  const col = view ? valueColumn(view) : null;
  const values = (customActive ? custom!.columns.score : (col && region?.columns[col])) || null;

  const candidates: Candidate[] = useMemo(() => {
    if (!index || !view) return [];
    if (view.kind === "target" && customActive) return custom!.candidates;
    const pool = worldwide && index.candidates_world ? index.candidates_world : index.candidates;
    if (view.kind === "target") return (pool[view.id] ?? []).slice(0, 10);
    if (view.kind === "classifier") return (pool["__classifier__"] ?? []).slice(0, 10);
    return [];
  }, [index, view, customActive, custom, worldwide]);
  const regionCandidates = candidates.filter((c) => c.region === region?.id);

  const pickCandidate = (c: Candidate) => {
    setFocus({ lon: c.lon, lat: c.lat, key: Date.now() });
    if (c.region !== regionId) {
      setPendingCell({ region: c.region, cell: c.cell });
      setRegionId(c.region);
    } else if (region) {
      const i = region.columns.cell.indexOf(c.cell);
      if (i >= 0) setSelected(i);
    }
  };

  if (error && !index) {
    return (
      <main className="empty">
        <h1>No data yet</h1>
        <p>{error}</p>
        <pre>{`cd ..\n.venv\\Scripts\\activate\nanalog demo          # synthetic test region\n# or real data:\nanalog region create my-site --bbox W S E N\nanalog fetch srtm my-site\nanalog run my-site\nanalog export        # also copies the JSON into frontend/public/data/`}</pre>
        <button onClick={reload}>Try again</button>
      </main>
    );
  }
  if (!index || !view) {
    return <AppSplash message="Loading the results…" detail="Scores, candidates and known analogs" />;
  }

  const regionSummary = index.regions.find((r) => r.id === regionId);
  const hasWorld = !!index.world && !!index.candidates_world;
  const isWorld = (r: { scope?: string }) => r.scope === "world";
  const shownRegions = index.regions.filter((r) => isWorld(r) === (hasWorld && worldwide));
  const toggleWorld = (on: boolean) => {
    setWorldwide(on);
    const pool = index.regions.filter((r) => isWorld(r) === on);
    if (!pool.some((r) => r.id === regionId)) setRegionId(pool[0]?.id ?? null);
  };
  const hoverValue = hover && values ? values[hover.i] : null;

  return (
    <div className="app" style={{ "--panel-w": `${panelW}px` } as React.CSSProperties}>
      <aside className="panel">
        <header className="brand">
          <h1>Earth Analog Explorer</h1>
          <p className="muted">Which places on Earth look like Moon and Mars base sites?</p>
        </header>

        <section>
          <label className="lbl" htmlFor="view">Colour the map by</label>
          <select id="view" value={viewKey(view)} onChange={(e) => { setView(parseViewKey(e.target.value)); }}>
            {(["moon", "mars", "earth"] as const).map((b) => {
              const ts = index.targets.filter((t) => t.body === b);
              return ts.length ? (
                <optgroup key={b} label={`Similarity to a ${BODY_LABEL[b]} site`}>
                  {ts.map((t) => <option key={t.id} value={`t:${t.id}`}>{t.name}</option>)}
                </optgroup>
              ) : null;
            })}
            {index.classifier && (
              <optgroup label="Classifier (learned from known analogs)">
                <option value="classifier">Any analog type</option>
                {index.classifier.classes.map((c) => <option key={c} value={`c:${c}`}>{humanize(c)}</option>)}
              </optgroup>
            )}
          </select>
        </section>

        {target && baseTarget && (
          <TargetCard target={target} index={index}
            weighting={index.backend?.score ? {
              base: baseTarget, weights, scoring, active: customActive,
              set: (k, w) => setWeights((cur) => ({ ...(cur ?? {}), [k]: w })),
              reset: () => setWeights(null),
            } : null} />
        )}
        {view.kind !== "target" && index.classifier && <ClassifierCard info={index.classifier} />}

        <section>
          {hasWorld && (
            <label className="check">
              <input type="checkbox" checked={worldwide} onChange={(e) => toggleWorld(e.target.checked)} />
              <span>
                <strong>Worldwide</strong> instead of the study regions
                <span className="muted small"> · Earth Engine screen: {index.world!.tiles} tiles of 5°,{" "}
                  {index.world!.cells.toLocaleString()} cells of ~5 km; the {index.world!.published_tiles} tiles holding a
                  top candidate can be opened</span>
              </span>
            </label>
          )}
          <label className="lbl" htmlFor="region">{worldwide && hasWorld ? "World tile" : "Earth region"}</label>
          <select id="region" value={regionId ?? ""} onChange={(e) => setRegionId(e.target.value)}>
            {shownRegions.map((r) => <option key={r.id} value={r.id}>{r.title} ({r.n_cells.toLocaleString()} cells)</option>)}
          </select>
          {regionSummary && (
            <p className="muted small">
              Layers: {regionSummary.layers.join(", ") || "none"}
              {loading && " · loading…"}
            </p>
          )}
        </section>

        <Legend opacity={opacity} setOpacity={setOpacity} classifier={view.kind !== "target"} />

        {selected !== null && region && (
          <CellDetail region={region} i={selected} target={target} view={view} index={index}
            score={values ? values[selected] : null} onClose={() => setSelected(null)} />
        )}

        <section>
          <h2>Top candidates {view.kind === "class" && <span className="muted small">(list available for targets and "any analog")</span>}</h2>
          {customActive && <p className="note small">Custom weights: candidates for <strong>this region only</strong>, re-scored by the backend.</p>}
          {index.exclusion_km !== undefined && (
            <p className="muted small">
              Known analogs ({index.known_analogs?.length ?? 0}) and everything within {index.exclusion_km} km of them are
              left out: they are used to test the method, not reported as discoveries.
              {view.kind === "target" && " Ordered by tier (A = most criteria met), then score."}
            </p>
          )}
          {candidates.length === 0 ? (
            <p className="muted small">No cell outside the known-analog zones meets a criterion for this view.</p>
          ) : (
            <ol className="cands">
              {candidates.map((c, k) => (
                <li key={`${c.region}-${c.cell}`}>
                  <button onClick={() => pickCandidate(c)}>
                    <span className="rank">{k + 1}</span>
                    {c.tier && <span className={`tier tier-${c.tier}`} title={`Tier ${c.tier}: ${c.met} of ${c.scored} criteria met`}>{c.tier}</span>}
                    <span className="grow">
                      {c.lat.toFixed(3)}, {c.lon.toFixed(3)}
                      <span className="muted small"> · {index.regions.find((r) => r.id === c.region)?.title ?? c.region}</span>
                    </span>
                    <span className="score">{c.score.toFixed(2)}</span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>

        <footer className="small">
          <div className="credits">
            <strong>{CREDITS.project}</strong>
            <span className="muted"> · {CREDITS.app}</span>
            <div>
              {CREDITS.team ? <>Team <strong>{CREDITS.team}</strong>: </> : "Team: "}
              {CREDITS.members.map((m, k) => (
                <span key={m.name}>
                  {k > 0 && ", "}
                  {m.url ? <a href={m.url} target="_blank" rel="noreferrer" title={`${m.name} on LinkedIn`}>{m.name}</a> : m.name}
                </span>
              ))}
            </div>
            <div className="muted">
              {CREDITS.event} · {CREDITS.location} ·{" "}
              <a href={CREDITS.repo} target="_blank" rel="noreferrer">Source code</a> · {CREDITS.license}
            </div>
          </div>
          <div className="muted">
            Data exported {new Date(index.generated).toLocaleString()} ·{" "}
            {source === "api" ? "served by the backend" : "static copy (backend not connected)"} ·{" "}
            <button className="link" onClick={reload}>Reload data</button>
          </div>
        </footer>
      </aside>

      <div className="resizer" role="separator" aria-orientation="vertical" aria-label="Resize the panel"
        title="Drag to resize the panel" onPointerDown={startResize} />

      <div className="mapwrap">
        <MapView
          region={region}
          values={values}
          selected={selected}
          onSelect={setSelected}
          onHover={setHover}
          candidates={regionCandidates}
          onCandidate={pickCandidate}
          focus={focus}
          opacity={opacity}
          onReady={() => setMapReady(true)}
        />
        {!mapReady ? (
          <MapLoading message="Loading the map…" detail="Base map and satellite imagery" />
        ) : (loading || (regionId && region?.id !== regionId)) && (
          <MapLoading message={`Loading ${regionSummary?.title ?? "region"}…`}
            detail={regionSummary ? `${regionSummary.n_cells.toLocaleString()} cells` : undefined} />
        )}
        {hover && region && (
          <div className="tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
            <strong>{hoverValue === null || hoverValue === undefined ? "No score" : `Score ${hoverValue.toFixed(2)}`}</strong>
            <div className="muted small">
              {(region.columns.lat[hover.i] as number).toFixed(3)}, {(region.columns.lon[hover.i] as number).toFixed(3)} · click for details
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

type Weighting = {
  base: Target; weights: Record<string, number> | null; scoring: boolean; active: boolean;
  set: (k: string, w: number) => void; reset: () => void;
};

function TargetCard({ target, index, weighting }: { target: Target; index: Index; weighting: Weighting | null }) {
  return (
    <section className="card">
      <div className="row">
        <span className={`badge ${target.body}`}>{BODY_LABEL[target.body]}</span>
        <span className="muted small">{target.category}</span>
      </div>
      <h2>{target.name}</h2>
      {target.description && <p className="small">{target.description}</p>}
      <table className="profile">
        <thead><tr><th>Feature</th><th>Target</th><th>Weight</th></tr></thead>
        <tbody>
          {Object.entries(target.features).map(([k, s]) => (
            <tr key={k} title={index.features[k]?.description}>
              <td>{index.features[k]?.label ?? humanize(k)}</td>
              <td className="num">{modeLabel(s)} {s.percentile == null ? unitOf(index, k) : ""}</td>
              <td className="num">
                {weighting ? (
                  <span className="weight">
                    <input type="range" min={0} max={3} step={0.1} value={s.weight ?? 1}
                      aria-label={`Weight of ${index.features[k]?.label ?? k}`}
                      onChange={(e) => weighting.set(k, Number(e.target.value))} />
                    <span className={(s.weight ?? 1) !== (weighting.base.features[k]?.weight ?? 1) ? "changed" : ""}>
                      {(s.weight ?? 1).toFixed(1)}
                    </span>
                  </span>
                ) : (s.weight ?? 1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {weighting && (
        <p className="muted small">
          Drag a weight to change how much a feature counts (0 = ignore it); the map and candidates are re-scored.
          {weighting.scoring && " Re-scoring…"}
          {weighting.weights && <> <button className="link" onClick={weighting.reset}>Reset weights</button></>}
        </p>
      )}
      <p className="muted small">
        Profile from <code>{target.source_file}</code>
        {target.reference_url && <> · <a href={target.reference_url} target="_blank" rel="noreferrer">Trek</a></>}
      </p>
    </section>
  );
}

function ClassifierCard({ info }: { info: NonNullable<Index["classifier"]> }) {
  return (
    <section className="card">
      <h2>PU classifier</h2>
      <p className="small">
        Random-forest bags trained on {info.n_positive.toLocaleString()} cells inside known analog sites vs.
        random background cells. Score = probability the cell is an analog.
      </p>
      <p className="muted small">Test: hide one known site, retrain, and check it still ranks in the top 10% of all cells.</p>
      {info.validation.length > 0 && (
        <table className="profile">
          <thead><tr><th>Held-out site</th><th>Beats background</th><th>Top 10%</th></tr></thead>
          <tbody>
            {info.validation.map((v) => (
              <tr key={String(v.site)}>
                <td>{String(v.site)}</td>
                <td className="num">{v.beats_background_pct !== undefined ? `${v.beats_background_pct}%` : String(v.note)}</td>
                <td className="num">{v.top10 === undefined ? "–" : v.top10 ? "✓ pass" : "✗ fail"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Legend({ opacity, setOpacity, classifier }: { opacity: number; setOpacity: (n: number) => void; classifier: boolean }) {
  const grad = `linear-gradient(to right, ${RAMP.map(([s, c]) => `${c} ${s * 100}%`).join(", ")})`;
  return (
    <section>
      <div className="legend-bar" style={{ background: grad }} />
      <div className="legend-labels small">
        <span>0 · no match</span>
        <span>{classifier ? "probability" : "similarity"}</span>
        <span>1 · strong match</span>
      </div>
      <p className="muted small">Uncoloured cells have too little data to score.</p>
      <label className="lbl small" htmlFor="op">Overlay opacity</label>
      <input id="op" type="range" min={0.1} max={1} step={0.05} value={opacity}
        onChange={(e) => setOpacity(Number(e.target.value))} />
    </section>
  );
}

function unitOf(index: Index, k: string) {
  const u = index.features[k]?.unit;
  return u && !["fraction", "0-1", "-1..1"].includes(u) ? u : "";
}

function CellDetail({ region, i, target, view, index, score, onClose }: {
  region: RegionData; i: number; target: Target | null; view: View; index: Index;
  score: number | null | undefined; onClose: () => void;
}) {
  const c = region.columns;
  const featureKeys = target ? Object.keys(target.features) : Object.keys(index.features).filter((k) => k in c);
  return (
    <section className="card detail">
      <div className="row">
        <h2 className="grow">Selected cell</h2>
        <button className="link" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <p className="small">
        {(c.lat[i] as number).toFixed(4)}, {(c.lon[i] as number).toFixed(4)} ·{" "}
        <a href={`https://www.google.com/maps/@${c.lat[i]},${c.lon[i]},13z/data=!3m1!1e3`} target="_blank" rel="noreferrer">open in Maps</a>
      </p>
      <p className="big">
        {score === null || score === undefined ? "No score" : score.toFixed(2)}
        <span className="muted small"> {view.kind === "target" ? "overall similarity" : "probability"}</span>
      </p>
      {typeof c.analog_km?.[i] === "number" && index.exclusion_km !== undefined && (c.analog_km[i] as number) < index.exclusion_km && (
        <p className="note small">
          Within {Math.round(c.analog_km[i] as number)} km of a known analog: shown for reference, never listed as a candidate.
        </p>
      )}
      {target && hasCriteria(index) && <CriteriaSection target={target} region={region} i={i} index={index} overall={score} />}
      <table className="profile">
        <thead>
          <tr><th>Feature</th><th>Here</th>{target && <><th>Target</th><th>Match</th></>}</tr>
        </thead>
        <tbody>
          {featureKeys.map((k) => {
            const x = c[k]?.[i];
            const spec = target?.features[k];
            const cmp = spec ? c[columnOf(k, spec)]?.[i] : undefined;  // rel__ normal score for percentile features
            const s = spec ? featureSimilarity(cmp, spec) : null;
            const rank = spec?.column && typeof cmp === "number" ? `${pct(normCdf(cmp))} on Earth` : "";
            return (
              <tr key={k} title={index.features[k]?.description}>
                <td>{index.features[k]?.label ?? humanize(k)}</td>
                <td className="num">{fmt(x, index.features[k]?.unit)}{rank && <div className="muted small">{rank}</div>}</td>
                {spec && (
                  <>
                    <td className="num muted">{modeLabel(spec)}</td>
                    <td>
                      {s === null ? <span className="muted small">no data</span> : (
                        <span className="match"><span className="bar" style={{ width: `${Math.round(s * 100)}%` }} /><span className="small">{Math.round(s * 100)}%</span></span>
                      )}
                    </td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      {index.backend?.notes && (
        <Notes region={region.id} cell={c.cell[i] as number} lat={c.lat[i] as number} lon={c.lon[i] as number}
          targetId={view.kind === "target" ? view.id : ""} />
      )}
      {view.kind !== "target" && index.classifier && (
        <table className="profile">
          <thead><tr><th>Class</th><th>Probability</th></tr></thead>
          <tbody>
            {index.classifier.classes.map((cl) => (
              <tr key={cl}><td>{humanize(cl)}</td><td className="num">{fmt(c[`prob__${cl}`]?.[i])}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function CriteriaSection({ target, region, i, index, overall }: {
  target: Target; region: RegionData; i: number; index: Index & CriteriaDefs; overall: number | null | undefined;
}) {
  const scores = criteriaScores(target, (n) => region.columns[n]?.[i], index, overall);
  const { tier, met, scored } = tierOf(scores, index);
  const tierInfo = index.tiers.find((t) => t.tier === tier);
  return (
    <div className="criteria">
      <p className="small">
        {tier ? <><span className={`tier tier-${tier}`}>{tier}</span> <strong>{tierInfo?.label}</strong>: </> : <strong>No tier: </strong>}
        {met} of {scored} measured criteria met (≥ {index.thresholds.met.toFixed(2)})
        {scored < scores.length && <span className="muted"> · {scores.length - scored} of the {scores.length} criteria for this {BODY_LABEL[target.body]} target have no data yet</span>}
      </p>
      <CriteriaRadar scores={scores} met={index.thresholds.met} />
      <table className="profile">
        <thead><tr><th>Criterion</th><th>Score</th><th>Result</th></tr></thead>
        <tbody>
          {scores.map((s) => (
            <tr key={s.k} title={`${s.label}${s.features.length ? " · from: " + s.features.map((n) => index.features[n]?.label ?? n).join(", ") : " · no feature for this criterion yet"}`}>
              <td>{s.k}. {s.short}</td>
              <td className="num">{s.score === null ? "–" : s.score.toFixed(2)}</td>
              <td><span className={`band band-${band(s.score, index).replace(" ", "-")}`}>{band(s.score, index)}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Notes({ region, cell, lat, lon, targetId }: {
  region: string; cell: number; lat: number; lon: number; targetId: string;
}) {
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [text, setText] = useState("");
  const [author, setAuthor] = useState(() => local.get("analog.author") ?? "");
  const [token, setToken] = useState(() => local.get("analog.teamToken") ?? "");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setNotes(null);
    getNotes(region, cell).then((n) => { if (live) setNotes(n); }).catch(() => { if (live) setNotes([]); });
    return () => { live = false; };
  }, [region, cell]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try {
      const n = await addNote({ region, cell, lat, lon, target_id: targetId, author, text }, token);
      local.set("analog.author", author);
      local.set("analog.teamToken", token);
      setNotes((cur) => [n, ...(cur ?? [])]);
      setText("");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="notes">
      <h3 className="small">Team notes</h3>
      {notes === null ? <p className="muted small">Loading…</p> : notes.length === 0 ? (
        <p className="muted small">No notes on this cell yet.</p>
      ) : (
        <ul>
          {notes.map((n) => (
            <li key={n.id} className="small">
              <strong>{n.author}</strong> <span className="muted">{new Date(n.created_at).toLocaleString()}</span>
              <div>{n.text}</div>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="note-form">
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={2000}
          placeholder="e.g. Checked imagery: open pit mine, not natural terrain" required />
        <div className="row">
          <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Your name" maxLength={80} required />
          <input value={token} onChange={(e) => setToken(e.target.value)} placeholder="Team token" type="password" required />
          <button type="submit">Add</button>
        </div>
        {msg && <p className="note small">{msg}</p>}
      </form>
    </div>
  );
}
