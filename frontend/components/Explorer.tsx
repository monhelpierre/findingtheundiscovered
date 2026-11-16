"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import MapView from "./MapView";
import MoonbaseCard, { loadMoonbase, MB_TIERS, moonbasePoints, type MoonbaseData } from "./Moonbase";
import CriteriaRadar from "./CriteriaRadar";
import { AppSplash, MapLoading } from "./Loader";
import { CREDITS } from "@/lib/credits";
import type { Candidate, CriteriaDefs, Index, Mission, RegionData, Target, View } from "@/lib/types";
import {
  addNote, getNotes, load, local, scoreRegion, type Note, type ScoreResult, type Source, type Verdict,
} from "@/lib/api";
import {
  band, columnOf, criteriaScores, criterionColumn, criterionFeatures, featureSimilarity, fmt, hasCriteria, humanize,
  modeLabel, normCdf, parseViewKey, pct, tierOf, topCells, valueColumn, viewKey,
} from "@/lib/similarity";
import { classOf, classRange, scoreClasses, type ScoreClass } from "@/lib/classes";
import { cellRows, downloadCsv, toCsv } from "@/lib/cellExport";
import {
  cellKey, confidence, download, LEVEL_LABEL, loadLocal, loadShared, mergeReviews, newId, saveLocal,
  type Check, type Confidence, type Review,
} from "@/lib/reviews";

const BODY_LABEL: Record<string, string> = { moon: "Moon", mars: "Mars", earth: "Earth" };
const MISSION_LABEL: Record<Mission, string> = {
  habitat: "Habitat: where a crew could live",
  landing: "Landing: where a spacecraft could land",
};
// Shapes as well as colours, so the verdict reads without colour vision.
const VERDICTS: { v: Verdict; icon: string; label: string }[] = [
  { v: "good", icon: "✓", label: "Good analog" },
  { v: "bad", icon: "✗", label: "Not an analog" },
  { v: "unsure", icon: "?", label: "Unsure" },
];

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
  // Hide cells (and candidates) scoring below this
  const [minScore, setMinScore] = useState(0);
  // Test one criterion alone (null: the full profile)
  const [criterion, setCriterion] = useState<number | null>(null);
  // Field reviews: the team's shared file + the ones made in this browser
  const [shared, setShared] = useState<Review[]>([]);
  const [mine, setMine] = useState<Review[]>([]);
  useEffect(() => {
    setMine(loadLocal());
    loadShared().then(setShared);
  }, []);
  const reviews = useMemo(() => mergeReviews(shared, mine), [shared, mine]);
  // Moon-base sites of the sister project (optional layer; only if the export has moonbase.json)
  const [mbAvailable, setMbAvailable] = useState(false);
  const [mbOn, setMbOn] = useState(false);
  const [mb, setMb] = useState<MoonbaseData | null>(null);
  const [mbTiers, setMbTiers] = useState<Set<string>>(() => new Set(MB_TIERS.slice(0, 3)));
  const [mbSel, setMbSel] = useState<number | null>(null);
  useEffect(() => {
    fetch("/data/moonbase.json", { method: "HEAD", cache: "no-store" })
      .then((r) => setMbAvailable(r.ok && (r.headers.get("content-type") ?? "").includes("json")))
      .catch(() => setMbAvailable(false));
  }, []);
  useEffect(() => { if (mbOn && !mb) loadMoonbase().then(setMb); }, [mbOn, mb]);
  const mbPoints = useMemo(() => (mbOn && mb ? moonbasePoints(mb, mbTiers) : null), [mbOn, mb, mbTiers]);
  const byCell = useMemo(() => {
    const m = new Map<string, Review[]>();
    for (const r of reviews) {
      const k = cellKey(r.region, r.cell);
      m.set(k, [...(m.get(k) ?? []), r]);
    }
    return m;
  }, [reviews]);
  const addReview = (r: Review) => setMine((cur) => { const next = [...cur, r]; saveLocal(next); return next; });
  const removeReview = (id: string) => setMine((cur) => { const next = cur.filter((r) => r.id !== id); saveLocal(next); return next; });
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
  const criteriaDefs = index && hasCriteria(index) ? index : null;
  // A criterion only makes sense for a target with features for it.
  const crit = criterion !== null && target && criteriaDefs && criterionFeatures(target, criterion, criteriaDefs).length
    ? criterion : null;
  const critValues = useMemo(
    () => (crit !== null && target && criteriaDefs && region ? criterionColumn(target, crit, region.columns, criteriaDefs) : null),
    [crit, target, criteriaDefs, region]);
  const values = critValues ?? ((customActive ? custom!.columns.score : (col && region?.columns[col])) || null);
  const classes = useMemo(() => scoreClasses(index?.thresholds), [index?.thresholds]);

  const candidates: Candidate[] = useMemo(() => {
    if (!index || !view) return [];
    const keep = (cs: Candidate[]) => cs.filter((c) => c.score >= minScore).slice(0, 10);
    // One criterion: best cells of the open region for that criterion alone (computed here).
    if (critValues && region) {
      return topCells(critValues, region.columns, region.id, { exclusionKm: index.exclusion_km, minScore: Math.max(0.2, minScore) });
    }
    if (view.kind === "target" && customActive) return keep(custom!.candidates);
    const pool = worldwide && index.candidates_world ? index.candidates_world : index.candidates;
    if (view.kind === "target") return keep(pool[view.id] ?? []);
    if (view.kind === "classifier") return keep(pool["__classifier__"] ?? []);
    return [];
  }, [index, view, customActive, custom, worldwide, minScore, critValues, region]);
  // Keep each candidate's rank in the full list, so the map numbers match the list.
  const regionCandidates = useMemo(
    () => candidates.map((c, k) => ({ ...c, rank: k + 1 })).filter((c) => c.region === region?.id),
    [candidates, region?.id]);
  // Another site or criterion: drop the old selection and zoom to the new candidates.
  const viewId = view ? viewKey(view) : "";
  const [refit, setRefit] = useState(0);
  useEffect(() => {
    setSelected(null);
    setHover(null);
    setRefit((n) => n + 1);
  }, [viewId, crit]);

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
  const counts = classes.map((c) => values ? values.filter((v) => typeof v === "number" && v >= c.min && (c.max >= 1 || v < c.max)).length : 0);
  const kept = values ? values.filter((v) => typeof v === "number" && v >= minScore).length : 0;
  const critInfo = crit !== null ? index.criteria?.[String(crit)] : null;
  // Share of the region's cells with data for each criterion of the target (to flag empty ones).
  const coverage = (k: number) => {
    if (!target || !criteriaDefs || !region) return 0;
    const v = criterionColumn(target, k, region.columns, criteriaDefs);
    return v.filter((x) => x !== null).length / Math.max(1, v.length);
  };
  // Targets grouped by mission (habitat / landing); targets without one fall back to their body.
  const groups: { key: string; label: string; targets: Target[] }[] = [
    ...(Object.keys(MISSION_LABEL) as Mission[]).map((m) => ({
      key: m, label: MISSION_LABEL[m], targets: index.targets.filter((t) => t.mission === m),
    })),
    ...(["moon", "mars", "earth"] as const).map((b) => ({
      key: b, label: `Other ${BODY_LABEL[b]} sites`,
      targets: index.targets.filter((t) => t.body === b && !(t.mission && t.mission in MISSION_LABEL)),
    })),
  ].filter((g) => g.targets.length);

  return (
    <div className="app" style={{ "--panel-w": `${panelW}px` } as React.CSSProperties}>
      <aside className="panel">
        <header className="brand">
          <h1>Earth Analog Explorer</h1>
          <p className="muted">Which places on Earth look like Moon and Mars base sites?</p>
        </header>

        <QuickLook />

        <section>
          <label className="lbl" htmlFor="view">Colour the map by similarity to</label>
          <select id="view" value={viewKey(view)} onChange={(e) => { setView(parseViewKey(e.target.value)); }}>
            {groups.map((g) => (
              <optgroup key={g.key} label={g.label}>
                {g.targets.map((t) => <option key={t.id} value={`t:${t.id}`}>{t.name} ({BODY_LABEL[t.body]})</option>)}
              </optgroup>
            ))}
            {index.classifier && (
              <optgroup label="Classifier (learned from known analogs)">
                <option value="classifier">Any analog type</option>
                {index.classifier.classes.map((c) => <option key={c} value={`c:${c}`}>{humanize(c)}</option>)}
              </optgroup>
            )}
          </select>
          {view.kind === "target" && target && criteriaDefs && (
            <>
              <label className="lbl" htmlFor="crit" style={{ marginTop: 10 }}>What to test</label>
              <select id="crit" value={crit ?? ""} onChange={(e) => setCriterion(e.target.value ? Number(e.target.value) : null)}>
                <option value="">All criteria (full mission profile)</option>
                <optgroup label="One criterion alone">
                  {(criteriaDefs.applies[target.body] ?? criteriaDefs.applies.earth).map((k) => {
                    const feats = criterionFeatures(target, k, criteriaDefs);
                    const cov = feats.length ? coverage(k) : 0;
                    const c = criteriaDefs.criteria[String(k)];
                    return (
                      <option key={k} value={k} disabled={!feats.length || cov === 0}>
                        {k}. {c?.short ?? `Criterion ${k}`}
                        {!feats.length ? " (no measure yet)" : cov === 0 ? " (no data in this region)" : cov < 0.5 ? ` (data for ${Math.round(cov * 100)}% of cells)` : ""}
                      </option>
                    );
                  })}
                </optgroup>
              </select>
              {critInfo && (
                <p className="note small">
                  Map and candidates show <strong>{critInfo.label.toLowerCase()}</strong> only, compared with{" "}
                  {target.name}: {criterionFeatures(target, crit!, criteriaDefs).map((n) => index.features[n]?.label ?? humanize(n)).join(", ")}.
                </p>
              )}
            </>
          )}
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

        {mbAvailable && (
          <MoonbaseCard on={mbOn} setOn={setMbOn} data={mb} tiers={mbTiers} setTiers={setMbTiers} selected={mbSel}
            onPick={(i) => {
              setMbSel(i);
              setFocus({ lon: mb!.columns.lon[i] as number, lat: mb!.columns.lat[i] as number, key: Date.now() });
            }} />
        )}

        <Legend opacity={opacity} setOpacity={setOpacity} classifier={view.kind !== "target"} classes={classes} counts={values ? counts : null}
          minScore={minScore} setMinScore={setMinScore} kept={values ? kept : null} total={region?.n_cells ?? 0} />

        {selected !== null && region && (
          <CellDetail region={region} i={selected} target={target} view={view} index={index}
            score={values ? values[selected] : null} onClose={() => setSelected(null)} classes={classes}
            scoreLabel={critInfo ? `${critInfo.short} only` : undefined}
            reviews={byCell.get(cellKey(region.id, region.columns.cell[selected] as number)) ?? []}
            allReviews={reviews} onReview={addReview} onRemove={removeReview} />
        )}

        <section>
          <h2>
            {critInfo ? <>Where to test: {critInfo.short.toLowerCase()}</> : "Top candidates"}
            {view.kind === "class" && <span className="muted small"> (list available for targets and "any analog")</span>}
          </h2>
          {critInfo && <p className="note small">Best cells of <strong>this region</strong> for this criterion alone, at least 15 km apart.</p>}
          {customActive && <p className="note small">Custom weights: candidates for <strong>this region only</strong>, re-scored by the backend.</p>}
          {index.exclusion_km !== undefined && (
            <p className="muted small">
              Known analogs ({index.known_analogs?.length ?? 0}) and everything within {index.exclusion_km} km of them are
              left out: they are used to test the method, not reported as discoveries.
              {view.kind === "target" && !critInfo && " Ordered by tier (A = most criteria met), then score."}
            </p>
          )}
          {candidates.length === 0 ? (
            <p className="muted small">
              {minScore > 0
                ? <>No candidate scores {minScore.toFixed(2)} or more. <button className="link" onClick={() => setMinScore(0)}>Clear the minimum</button></>
                : "No cell outside the known-analog zones meets a criterion for this view."}
            </p>
          ) : (
            <ol className="cands">
              {candidates.map((c, k) => {
                const conf = confidence(byCell.get(cellKey(c.region, c.cell)) ?? []);
                const cls = classOf(c.score, classes);
                return (
                <li key={`${c.region}-${c.cell}`}>
                  <button onClick={() => pickCandidate(c)}>
                    <span className="rank">{k + 1}</span>
                    {c.tier && <span className={`tier tier-${c.tier}`} title={`Tier ${c.tier}: ${c.met} of ${c.scored} criteria met`}>{c.tier}</span>}
                    <span className="grow">
                      {c.lat.toFixed(3)}, {c.lon.toFixed(3)}
                      <span className="muted small"> · {index.regions.find((r) => r.id === c.region)?.title ?? c.region}</span>
                    </span>
                    {conf && <ConfidenceBadge conf={conf} compact />}
                    <span className="score" title={`${cls.label} (${classRange(cls)})`}>
                      <span className="swatch" style={{ background: cls.color }} aria-hidden />
                      {c.score.toFixed(2)}
                    </span>
                  </button>
                </li>
                );
              })}
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
          refit={refit}
          moonbase={mbPoints}
          onMoonbase={setMbSel}
          onCandidate={pickCandidate}
          focus={focus}
          opacity={opacity}
          minScore={minScore}
          classes={classes}
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
            <strong>{hoverValue === null || hoverValue === undefined ? "No score"
              : `${classOf(hoverValue, classes).label} · ${hoverValue.toFixed(2)}`}</strong>
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
        {target.mission && <span className="badge mission">{target.mission === "habitat" ? "Habitat" : "Landing"}</span>}
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

function Legend({ opacity, setOpacity, classifier, classes, counts, minScore, setMinScore, kept, total }: {
  opacity: number; setOpacity: (n: number) => void; classifier: boolean; classes: ScoreClass[]; counts: number[] | null;
  minScore: number; setMinScore: (n: number) => void; kept: number | null; total: number;
}) {
  // Hard colour stops: the slider track shows the same five classes as the map.
  const steps = `linear-gradient(to right, ${classes.map((c) => `${c.color} ${c.min * 100}% ${c.max * 100}%`).join(", ")})`;
  return (
    <section>
      <h2 className="small legend-title">{classifier ? "Probability" : "Similarity"} classes</h2>
      <ul className="legend-classes">
        {[...classes].reverse().map((c) => {
          const off = c.max <= minScore + 1e-9;
          return (
            <li key={c.k} className={off ? "off" : ""}>
              <button type="button" onClick={() => setMinScore(c.min === minScore ? 0 : c.min)}
                title={c.min === minScore ? "Show all classes" : `Show ${c.label.toLowerCase()} and above only`}>
                <span className="legend-swatch" style={{ background: c.color }} aria-hidden />
                <span className="grow"><strong>{c.label}</strong> <span className="muted">{classRange(c)}</span></span>
                {counts && <span className="num muted">{counts[c.k].toLocaleString()}</span>}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="row minscore">
        <label className="lbl small grow" htmlFor="min">Minimum score</label>
        <strong className="small">{minScore === 0 ? "all classes" : `≥ ${minScore.toFixed(2)}`}</strong>
      </div>
      <input id="min" type="range" min={0} max={0.95} step={0.05} value={minScore}
        className="ramp-range" style={{ "--ramp": steps } as React.CSSProperties}
        aria-valuetext={minScore === 0 ? "all classes" : `at least ${minScore.toFixed(2)}`}
        onChange={(e) => setMinScore(Number(e.target.value))} />
      <p className="muted small">
        Click a class to keep it and the ones above.{" "}
        {kept !== null && minScore > 0 && <>{kept.toLocaleString()} of {total.toLocaleString()} cells shown. </>}
        Uncoloured cells have too little data to score{minScore > 0 && " or fall below the minimum"}.
      </p>
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

function CellDetail({ region, i, target, view, index, score, onClose, classes, scoreLabel, reviews, allReviews, onReview, onRemove }: {
  region: RegionData; i: number; target: Target | null; view: View; index: Index;
  score: number | null | undefined; onClose: () => void; classes: ScoreClass[]; scoreLabel?: string;
  reviews: (Review & { mine?: boolean })[]; allReviews: (Review & { mine?: boolean })[];
  onReview: (r: Review) => void; onRemove: (id: string) => void;
}) {
  const c = region.columns;
  const conf = confidence(reviews);
  const exportCsv = () => {
    const rows = cellRows({
      region, i, index, target, score, classes, conf,
      scoreLabel: scoreLabel ?? (view.kind === "target" ? "overall similarity" : "probability"),
      regionTitle: index.regions.find((r) => r.id === region.id)?.title,
    });
    const what = target ? target.id : view.kind === "class" ? view.cls : "classifier";
    downloadCsv(`site_${region.id}_${c.cell[i]}_${what}.csv`, toCsv(rows));
  };
  const featureKeys = target ? Object.keys(target.features) : Object.keys(index.features).filter((k) => k in c);
  return (
    <section className="card detail">
      <div className="row">
        <h2 className="grow">Selected cell</h2>
        <button type="button" className="btn-small" onClick={exportCsv}
          title="Site, criteria, features and field reviews as a spreadsheet">Download CSV</button>
        <button className="link" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <p className="small">
        {(c.lat[i] as number).toFixed(4)}, {(c.lon[i] as number).toFixed(4)} ·{" "}
        <a href={`https://www.google.com/maps/@${c.lat[i]},${c.lon[i]},13z/data=!3m1!1e3`} target="_blank" rel="noreferrer">open in Maps</a>
      </p>
      <p className="big">
        {score === null || score === undefined ? "No score" : <>
          <span className="legend-swatch" style={{ background: classOf(score, classes).color }} aria-hidden />
          {score.toFixed(2)} <span className="small">{classOf(score, classes).label}</span>
        </>}
        <span className="muted small"> · {scoreLabel ?? (view.kind === "target" ? "overall similarity" : "probability")}</span>
      </p>
      {conf ? <ConfidenceBadge conf={conf} /> : <p className="muted small">No field review yet: has anyone been there?</p>}
      {typeof c.analog_km?.[i] === "number" && index.exclusion_km !== undefined && (c.analog_km[i] as number) < index.exclusion_km && (
        <p className="note small">
          Within {Math.round(c.analog_km[i] as number)} km of a known analog: shown for reference, never listed as a candidate.
        </p>
      )}
      {target && hasCriteria(index) && <CriteriaSection target={target} region={region} i={i} index={index} overall={score} conf={conf} />}
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
      <FieldReview region={region} i={i} target={target} index={index} reviews={reviews} allReviews={allReviews}
        onReview={onReview} onRemove={onRemove} />
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

function CriteriaSection({ target, region, i, index, overall, conf }: {
  target: Target; region: RegionData; i: number; index: Index & CriteriaDefs; overall: number | null | undefined;
  conf: Confidence | null;
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
        <thead><tr><th>Criterion</th><th>Score</th><th>Result</th>{conf && <th title="Field reviews: data matched / wrong (site visits count twice)">On site</th>}</tr></thead>
        <tbody>
          {scores.map((s) => (
            <tr key={s.k} title={`${s.label}${s.features.length ? " · from: " + s.features.map((n) => index.features[n]?.label ?? n).join(", ") : " · no feature for this criterion yet"}`}>
              <td>{s.k}. {s.short}</td>
              <td className="num">{s.score === null ? "–" : s.score.toFixed(2)}</td>
              <td><span className={`band band-${band(s.score, index).replace(" ", "-")}`}>{band(s.score, index)}</span></td>
              {conf && (
                <td className="num">
                  {conf.criteria[String(s.k)] ? (
                    <span className={conf.criteria[String(s.k)].wrong > conf.criteria[String(s.k)].ok ? "contested" : ""}
                      title={conf.criteria[String(s.k)].wrong > conf.criteria[String(s.k)].ok ? "Reviewers say this data is wrong here" : undefined}>
                      ✓{conf.criteria[String(s.k)].ok} ✗{conf.criteria[String(s.k)].wrong}
                    </span>
                  ) : <span className="muted">–</span>}
                </td>
              )}
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
  const [verdict, setVerdict] = useState<Verdict | "">("");
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
      if (!verdict && !text.trim()) { setMsg("Pick a verdict or write a note."); return; }
      const n = await addNote({ region, cell, lat, lon, target_id: targetId, author, verdict, text }, token);
      local.set("analog.author", author);
      local.set("analog.teamToken", token);
      setNotes((cur) => [n, ...(cur ?? [])]);
      setText("");
      setVerdict("");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="notes">
      <h3 className="small">Team notes (backend)</h3>
      {notes === null ? <p className="muted small">Loading…</p> : notes.length === 0 ? (
        <p className="muted small">No team note on this cell yet.</p>
      ) : (
        <ul>
          {notes.map((n) => (
            <li key={n.id} className="small">
              {n.verdict && <VerdictTag v={n.verdict} />}
              <strong>{n.author}</strong> <span className="muted">{new Date(n.created_at).toLocaleString()}</span>
              {n.text && <div>{n.text}</div>}
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="note-form">
        <div className="verdicts" role="radiogroup" aria-label="Your verdict on this result">
          {VERDICTS.map((x) => (
            <button key={x.v} type="button" role="radio" aria-checked={verdict === x.v}
              className={`verdict verdict-${x.v}${verdict === x.v ? " on" : ""}`}
              onClick={() => setVerdict(verdict === x.v ? "" : x.v)}>
              <span aria-hidden>{x.icon}</span> {x.label}
            </button>
          ))}
        </div>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={2000}
          placeholder="Optional: why? e.g. Checked imagery: open pit mine, not natural terrain" />
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

function VerdictTag({ v }: { v: Verdict }) {
  const x = VERDICTS.find((d) => d.v === v)!;
  return <span className={`verdict-tag verdict-${v}`}><span aria-hidden>{x.icon}</span> {x.label}</span>;
}

/** Confidence indicator from field reviews: label + icon (not colour alone). */
function ConfidenceBadge({ conf, compact = false }: { conf: Confidence; compact?: boolean }) {
  const icon = { high: "●", medium: "◐", low: "○", contested: "⚠" }[conf.level];
  const detail = [
    `${conf.n} review${conf.n > 1 ? "s" : ""}${conf.field ? `, ${conf.field} on site` : ", imagery only"}`,
    conf.agreement !== null ? `data matched in ${Math.round(conf.agreement * 100)}% of checks` : null,
    conf.reliability !== null ? `reliability ${conf.reliability.toFixed(1)}/5` : null,
  ].filter(Boolean).join(" · ");
  if (compact) {
    return (
      <span className={`conf conf-${conf.level} small`} title={`${LEVEL_LABEL[conf.level]}: ${detail}`}>
        <span aria-hidden>{icon}</span> {conf.n}
      </span>
    );
  }
  return (
    <div className={`conf-card conf-${conf.level}`}>
      <strong><span aria-hidden>{icon}</span> {LEVEL_LABEL[conf.level]}</strong>
      <div className="small">{detail}</div>
      {(conf.verdicts.good + conf.verdicts.bad + conf.verdicts.unsure) > 0 && (
        <div className="small">
          {VERDICTS.filter((x) => conf.verdicts[x.v]).map((x) => (
            <span key={x.v} className={`verdict-tag verdict-${x.v}`}><span aria-hidden>{x.icon}</span> {x.label} × {conf.verdicts[x.v]}</span>
          ))}
        </div>
      )}
    </div>
  );
}

const RELIABILITY = ["", "Wrong", "Poor", "Fair", "Good", "Exact"];

/** Field review form: did the satellite data and the scores match what you saw? No backend: saved in this
 *  browser; "Download" gives a file to add to public/reviews.json so everyone sees it. */
function FieldReview({ region, i, target, index, reviews, allReviews, onReview, onRemove }: {
  region: RegionData; i: number; target: Target | null; index: Index;
  reviews: (Review & { mine?: boolean })[]; allReviews: (Review & { mine?: boolean })[];
  onReview: (r: Review) => void; onRemove: (id: string) => void;
}) {
  const c = region.columns;
  const [open, setOpen] = useState(false);
  const [method, setMethod] = useState<Review["method"]>("field");
  const [verdict, setVerdict] = useState<Verdict | "">("");
  const [checks, setChecks] = useState<Record<string, Check>>({});
  const [reliability, setReliability] = useState(0);
  const [text, setText] = useState("");
  const [author, setAuthor] = useState(() => local.get("analog.author") ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  const crits = target && hasCriteria(index)
    ? (index.applies[target.body] ?? index.applies.earth).filter((k) => criterionFeatures(target, k, index).length)
    : [];
  const myCount = allReviews.filter((r) => r.mine).length;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!verdict && !reliability && !Object.keys(checks).length) {
      setMsg("Rate at least one thing: a criterion, the reliability or your verdict.");
      return;
    }
    onReview({
      id: newId(), region: region.id, cell: c.cell[i] as number, lat: c.lat[i] as number, lon: c.lon[i] as number,
      target_id: target?.id ?? "", method, verdict, checks, reliability, author: author.trim() || "Anonymous",
      text: text.trim(), date: new Date().toISOString(),
    });
    local.set("analog.author", author);
    setVerdict(""); setChecks({}); setReliability(0); setText(""); setMsg("Saved in this browser. Download your reviews to share them."); setOpen(false);
  };

  return (
    <div className="notes field-review">
      <div className="row">
        <h3 className="small grow">Field reviews</h3>
        {!open && <button type="button" className="btn-small" onClick={() => { setOpen(true); setMsg(null); }}>+ Review this site</button>}
      </div>
      {reviews.length > 0 && (
        <ul>
          {reviews.map((r) => (
            <li key={r.id} className="small">
              <span className="method">{r.method === "field" ? "On site" : "Imagery"}</span>
              {r.verdict && <VerdictTag v={r.verdict} />}
              <strong>{r.author}</strong> <span className="muted">{new Date(r.date).toLocaleDateString()}</span>
              {r.mine && <> <span className="muted">(this browser)</span> <button className="link" onClick={() => onRemove(r.id)}>delete</button></>}
              <div className="muted">
                {r.reliability > 0 && <>Reliability {r.reliability}/5 ({RELIABILITY[r.reliability]}). </>}
                {Object.entries(r.checks ?? {}).map(([k, v]) => `${index.criteria?.[k]?.short ?? k} ${v === "ok" ? "✓" : "✗"}`).join(" · ")}
              </div>
              {r.text && <div>{r.text}</div>}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <form onSubmit={submit} className="note-form">
          <fieldset>
            <legend className="small">How did you check?</legend>
            <div className="seg">
              {(["field", "imagery"] as const).map((m) => (
                <button key={m} type="button" aria-pressed={method === m} className={method === m ? "on" : ""} onClick={() => setMethod(m)}>
                  {m === "field" ? "I visited the site" : "Satellite imagery only"}
                </button>
              ))}
            </div>
          </fieldset>
          {crits.length > 0 && (
            <fieldset>
              <legend className="small">Does the data shown match what you saw?</legend>
              <table className="profile checks">
                <tbody>
                  {crits.map((k) => {
                    const key = String(k);
                    const set = (v: Check | null) => setChecks((cur) => {
                      const next = { ...cur };
                      if (v) next[key] = v; else delete next[key];
                      return next;
                    });
                    return (
                      <tr key={k}>
                        <td>{k}. {index.criteria?.[key]?.short}</td>
                        <td className="seg">
                          <button type="button" aria-pressed={checks[key] === "ok"} className={checks[key] === "ok" ? "on ok" : ""}
                            onClick={() => set(checks[key] === "ok" ? null : "ok")}>✓ Matches</button>
                          <button type="button" aria-pressed={checks[key] === "wrong"} className={checks[key] === "wrong" ? "on wrong" : ""}
                            onClick={() => set(checks[key] === "wrong" ? null : "wrong")}>✗ Wrong</button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="muted small">Leave a criterion blank if you didn't check it.</p>
            </fieldset>
          )}
          <fieldset>
            <legend className="small">Overall, how reliable are the data and the score here?</legend>
            <div className="seg">
              {[1, 2, 3, 4, 5].map((n) => (
                <button key={n} type="button" aria-pressed={reliability === n} className={reliability === n ? "on" : ""}
                  onClick={() => setReliability(reliability === n ? 0 : n)} title={RELIABILITY[n]}>
                  {n}<span className="sr"> {RELIABILITY[n]}</span>
                </button>
              ))}
            </div>
            <div className="legend-labels small"><span>1 · wrong</span><span>5 · exact</span></div>
          </fieldset>
          <fieldset>
            <legend className="small">Is it a good analog?</legend>
            <div className="verdicts" role="radiogroup" aria-label="Your verdict">
              {VERDICTS.map((x) => (
                <button key={x.v} type="button" role="radio" aria-checked={verdict === x.v}
                  className={`verdict verdict-${x.v}${verdict === x.v ? " on" : ""}`}
                  onClick={() => setVerdict(verdict === x.v ? "" : x.v)}>
                  <span aria-hidden>{x.icon}</span> {x.label}
                </button>
              ))}
            </div>
          </fieldset>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={2000}
            placeholder="What did you see? e.g. Slope is gentler than shown (about 8°); basalt, no clay" />
          <div className="row">
            <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Your name (optional)" maxLength={80} />
            <button type="submit">Save</button>
            <button type="button" className="link" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </form>
      )}
      {msg && <p className="note small">{msg}</p>}
      {myCount > 0 && (
        <p className="muted small">
          {myCount} review{myCount > 1 ? "s" : ""} saved in this browser only.{" "}
          <button className="link" onClick={() => download(allReviews.filter((r) => r.mine))}>Download them</button> and send the file to
          the team to share them with everyone.
        </p>
      )}
    </div>
  );
}

/** "No time? The idea in 30 seconds": a 4-step picture of the method. */
function QuickLook() {
  const [open, setOpen] = useState(() => local.get("analog.quicklook") !== "closed");
  return (
    <details className="card quicklook" open={open}
      onToggle={(e) => {
        const o = (e.currentTarget as HTMLDetailsElement).open;
        setOpen(o);
        local.set("analog.quicklook", o ? "open" : "closed");
      }}>
      <summary><strong>No time? The idea in 30 seconds</strong></summary>
      <svg viewBox="0 0 340 120" role="img" className="ql-svg"
        aria-label="1. Pick a Moon or Mars site. 2. Measure every cell of an Earth region. 3. Score how alike they are, 0 to 1. 4. Get a shortlist of places to visit.">
        <defs>
          <linearGradient id="ql-ramp">
            {scoreClasses().flatMap((c) => [
              <stop key={`${c.k}a`} offset={c.min} stopColor={c.color} />, <stop key={`${c.k}b`} offset={c.max} stopColor={c.color} />,
            ])}
          </linearGradient>
          <marker id="ql-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L6,3 L0,6 z" fill="currentColor" />
          </marker>
        </defs>
        {/* 1: the target site */}
        <circle cx="38" cy="42" r="22" fill="var(--moon)" />
        <circle cx="30" cy="36" r="4" fill="var(--surface)" opacity=".5" />
        <circle cx="45" cy="50" r="6" fill="var(--surface)" opacity=".5" />
        {/* 2: the Earth grid */}
        {Array.from({ length: 16 }, (_, k) => (
          <rect key={k} x={106 + (k % 4) * 11} y={22 + Math.floor(k / 4) * 11} width="10" height="10" rx="1.5"
            fill="var(--line)" stroke="var(--muted)" strokeWidth=".5" />
        ))}
        {/* 3: scored cells */}
        {[0.1, 0.3, 0.55, 0.95, 0.2, 0.7, 0.4, 0.85, 0.05].map((v, k) => (
          <rect key={k} x={190 + (k % 3) * 14} y={22 + Math.floor(k / 3) * 14} width="13" height="13" rx="1.5" fill={classOf(v, scoreClasses()).color} />
        ))}
        {/* 4: the shortlist */}
        {[0, 1, 2].map((k) => (
          <g key={k}>
            <circle cx="276" cy={28 + k * 16} r="6.5" fill="#0d366b" stroke="#fff" strokeWidth="1.5" />
            <text x="276" y={31 + k * 16} textAnchor="middle" fontSize="8" fontWeight="700" fill="#fff">{k + 1}</text>
            <rect x="287" y={24 + k * 16} width={42 - k * 8} height="8" rx="2" fill={classOf(0.95 - k * 0.12, scoreClasses()).color} />
          </g>
        ))}
        <g stroke="currentColor" strokeWidth="1.2" fill="none" markerEnd="url(#ql-arrow)" opacity=".6">
          <path d="M66 42 H98" /><path d="M154 42 H182" /><path d="M236 42 H262" />
        </g>
        <g fontSize="9.5" textAnchor="middle" fill="currentColor">
          <text x="38" y="84" fontWeight="700">1. Pick a site</text>
          <text x="38" y="96" opacity=".7">on the Moon / Mars</text>
          <text x="128" y="84" fontWeight="700">2. Measure Earth</text>
          <text x="128" y="96" opacity=".7">cell by cell</text>
          <text x="210" y="84" fontWeight="700">3. Classify</text>
          <text x="210" y="96" opacity=".7">very low → very high</text>
          <text x="300" y="84" fontWeight="700">4. Shortlist</text>
          <text x="300" y="96" opacity=".7">places to visit</text>
        </g>
        <rect x="150" y="106" width="120" height="6" rx="3" fill="url(#ql-ramp)" />
        <text x="146" y="112" fontSize="8" textAnchor="end" fill="currentColor" opacity=".7">different</text>
        <text x="274" y="112" fontSize="8" fill="currentColor" opacity=".7">very alike</text>
      </svg>
      <ol className="small ql-steps">
        <li>Choose a <strong>habitat</strong> or <strong>landing</strong> site in the list below.</li>
        <li>Or pick <strong>What to test</strong> (slope, mineralogy…) to see where to test one criterion alone.</li>
        <li>Click a class in the legend to keep only the best cells (yellow = very high).</li>
        <li>Been there? Click a numbered marker and say if the data matched what you saw.</li>
      </ol>
    </details>
  );
}
