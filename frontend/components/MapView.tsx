"use client";

import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import type { Candidate, RegionData } from "@/lib/types";
import { CLASSES, type ScoreClass } from "@/lib/classes";

// Satellite basemaps. Esri has "Map data not yet available" gaps at close zoom
// in many regions, so the user can switch to Google or Bing imagery there.
const BASEMAPS = {
  google: {
    label: "Google",
    tiles: [0, 1, 2, 3].map((n) => `https://mt${n}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}`),
    maxzoom: 20,
    attribution: "Imagery © Google",
  },
  bing: {
    label: "Bing",
    tiles: [0, 1, 2, 3].map((n) => `https://ecn.t${n}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=1`),
    maxzoom: 19,
    attribution: "Imagery © Microsoft",
  },
  esri: {
    label: "Esri",
    tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    maxzoom: 18,
    attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
  },
} as const;
type BasemapId = keyof typeof BASEMAPS;
const BASEMAP_IDS = Object.keys(BASEMAPS) as BasemapId[];
const BASEMAP_KEY = "basemap";

function initialBasemap(): BasemapId {
  try {
    const v = localStorage.getItem(BASEMAP_KEY);
    if (v && v in BASEMAPS) return v as BasemapId;
  } catch {}
  return "google";
}

type Props = {
  region: RegionData | null;
  values: (number | null)[] | null;
  selected: number | null;
  onSelect: (i: number | null) => void;
  onHover: (h: { i: number; x: number; y: number } | null) => void;
  /** Candidates in this region, with their rank in the full list (the number shown). */
  candidates: (Candidate & { rank: number })[];
  onCandidate: (c: Candidate) => void;
  focus: { lon: number; lat: number; key: number } | null;
  /** Changes when the user picks another site or criterion: zoom out to the new candidates. */
  refit?: number;
  opacity: number;
  /** Cells scoring below this are hidden (still clickable). */
  minScore: number;
  /** Score classes (colour steps); defaults to the standard five. */
  classes?: ScoreClass[];
  /** Called once the base map has loaded (the app hides its loading overlay). */
  onReady?: () => void;
};

function cellPolygon(lon: number, lat: number, half: number) {
  return {
    type: "Polygon" as const,
    coordinates: [[
      [lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
      [lon - half, lat + half], [lon - half, lat - half],
    ]],
  };
}

export default function MapView(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const cb = useRef(p);
  cb.current = p;
  const lastRegion = useRef<string | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const [basemap, setBasemap] = useState<BasemapId>(initialBasemap);
  const basemapRef = useRef(basemap);

  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: {
        version: 8,
        sources: Object.fromEntries(BASEMAP_IDS.map((id) => [id, {
          type: "raster" as const, tiles: [...BASEMAPS[id].tiles], tileSize: 256,
          maxzoom: BASEMAPS[id].maxzoom, attribution: BASEMAPS[id].attribution,
        }])),
        layers: BASEMAP_IDS.map((id) => ({
          id: `basemap-${id}`, type: "raster" as const, source: id,
          layout: { visibility: id === basemapRef.current ? "visible" as const : "none" as const },
        })),
      },
      center: [0, 15],
      zoom: 1.4,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    m.on("load", () => {
      m.addSource("cells", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addSource("selected", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addSource("bbox", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: "cells", type: "fill", source: "cells",
        paint: { "fill-antialias": false, "fill-color": "#000", "fill-opacity": 0 },
      });
      m.addLayer({
        id: "bbox", type: "line", source: "bbox",
        paint: { "line-color": "#ffffff", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.8 },
      });
      m.addLayer({
        id: "selected-halo", type: "line", source: "selected",
        paint: { "line-color": "#000000", "line-width": 5, "line-opacity": 0.5 },
      });
      m.addLayer({
        id: "selected", type: "line", source: "selected",
        paint: { "line-color": "#ffffff", "line-width": 2.5 },
      });
      m.on("click", "cells", (e) => {
        const f = e.features?.[0];
        if (f && typeof f.id === "number") cb.current.onSelect(f.id);
      });
      m.on("mousemove", "cells", (e) => {
        const f = e.features?.[0];
        m.getCanvas().style.cursor = f ? "pointer" : "";
        if (f && typeof f.id === "number") cb.current.onHover({ i: f.id, x: e.point.x, y: e.point.y });
      });
      m.on("mouseleave", "cells", () => {
        m.getCanvas().style.cursor = "";
        cb.current.onHover(null);
      });
      setReady(true);
      cb.current.onReady?.();
    });
    map.current = m;
    // The side panel can be resized: keep the map filling its container.
    const ro = new ResizeObserver(() => m.resize());
    if (el.current) ro.observe(el.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
    };
  }, []);

  // Switch the visible basemap (hidden raster layers fetch no tiles).
  useEffect(() => {
    basemapRef.current = basemap;
    try { localStorage.setItem(BASEMAP_KEY, basemap); } catch {}
    const m = map.current;
    if (!ready || !m) return;
    for (const id of BASEMAP_IDS) {
      m.setLayoutProperty(`basemap-${id}`, "visibility", id === basemap ? "visible" : "none");
    }
  }, [ready, basemap]);

  // Rebuild cell polygons when the region or the coloured column changes.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const src = m.getSource("cells") as GeoJSONSource;
    const bbox = m.getSource("bbox") as GeoJSONSource;
    if (!p.region) {
      src.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    const { lon, lat } = p.region.columns;
    const half = p.region.cell_deg / 2;
    const features = lon.map((x, i) => ({
      type: "Feature" as const,
      id: i,
      properties: { v: p.values?.[i] ?? -1 },
      geometry: cellPolygon(x as number, lat[i] as number, half),
    }));
    src.setData({ type: "FeatureCollection", features });
    const [w, s, e, n] = p.region.bbox;
    bbox.setData({
      type: "Feature", properties: {},
      geometry: { type: "LineString", coordinates: [[w, s], [e, s], [e, n], [w, n], [w, s]] },
    });
    if (lastRegion.current !== p.region.id) {
      lastRegion.current = p.region.id;
      m.fitBounds([[w, s], [e, n]], { padding: 48, duration: 900 });
    }
  }, [ready, p.region, p.values]);

  // Colour + opacity (v = -1 means "no score": transparent but still clickable).
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const v = ["get", "v"];
    const cls = p.classes ?? CLASSES;
    // One flat colour per class (no gradient); the lowest class is lighter so the imagery shows through.
    m.setPaintProperty("cells", "fill-color", [
      "step", v, cls[0].color, ...cls.slice(1).flatMap((c) => [c.min, c.color]),
    ] as unknown as maplibregl.ExpressionSpecification);
    m.setPaintProperty("cells", "fill-opacity", [
      "case", ["<", v, Math.max(0, p.minScore)], 0,
      ["*", p.opacity, ["step", v, 0.3, cls[1].min, 0.8]],
    ] as unknown as maplibregl.ExpressionSpecification);
  }, [ready, p.opacity, p.minScore, p.classes]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const src = m.getSource("selected") as GeoJSONSource;
    if (p.selected === null || !p.region) {
      src.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    const i = p.selected;
    const half = p.region.cell_deg / 2;
    src.setData({
      type: "Feature", properties: {},
      geometry: cellPolygon(p.region.columns.lon[i] as number, p.region.columns.lat[i] as number, half),
    });
  }, [ready, p.selected, p.region]);

  // Numbered candidate markers.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    markers.current.forEach((mk) => mk.remove());
    markers.current = p.candidates.map((c, k) => {
      const d = document.createElement("button");
      d.className = "cand-marker";
      d.textContent = String(c.rank);
      d.title = `Candidate ${c.rank}: score ${c.score.toFixed(2)}`;
      d.onclick = (ev) => {
        ev.stopPropagation();
        cb.current.onCandidate(c);
      };
      return new maplibregl.Marker({ element: d }).setLngLat([c.lon, c.lat]).addTo(m);
    });
  }, [ready, p.candidates]);

  // New site or criterion: show its candidates (or the whole region if none is here).
  const refitDone = useRef(p.refit);
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !p.region || p.refit === refitDone.current) return;
    refitDone.current = p.refit;
    const pts = p.candidates.map((c) => [c.lon, c.lat]);
    if (pts.length) {
      const pad = p.region.cell_deg * 3;
      const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
      m.fitBounds([[Math.min(...xs) - pad, Math.min(...ys) - pad], [Math.max(...xs) + pad, Math.max(...ys) + pad]],
        { padding: 60, duration: 900, maxZoom: 11 });
    } else {
      const [w, s, e, n] = p.region.bbox;
      m.fitBounds([[w, s], [e, n]], { padding: 48, duration: 900 });
    }
  }, [ready, p.refit, p.candidates, p.region]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !p.focus) return;
    m.flyTo({ center: [p.focus.lon, p.focus.lat], zoom: Math.max(m.getZoom(), 11), duration: 900 });
  }, [ready, p.focus]);

  return (
    <>
      <div ref={el} className="map" />
      <div className="basemap-switch" role="radiogroup" aria-label="Basemap">
        {BASEMAP_IDS.map((id) => (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={basemap === id}
            className={basemap === id ? "on" : ""}
            onClick={() => setBasemap(id)}
          >
            {BASEMAPS[id].label}
          </button>
        ))}
      </div>
    </>
  );
}
