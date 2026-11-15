"use client";

import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import type { Candidate, RegionData } from "@/lib/types";

// Sequential one-hue ramp (light -> dark = low -> high).
export const RAMP: [number, string][] = [
  [0, "#cde2fb"],
  [0.25, "#9ec5f4"],
  [0.5, "#5598e7"],
  [0.75, "#256abf"],
  [1, "#0d366b"],
];

const IMAGERY =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

type Props = {
  region: RegionData | null;
  values: (number | null)[] | null;
  selected: number | null;
  onSelect: (i: number | null) => void;
  onHover: (h: { i: number; x: number; y: number } | null) => void;
  candidates: Candidate[];
  onCandidate: (c: Candidate) => void;
  focus: { lon: number; lat: number; key: number } | null;
  opacity: number;
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

  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: {
        version: 8,
        sources: {
          imagery: {
            type: "raster", tiles: [IMAGERY], tileSize: 256, maxzoom: 18,
            attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
          },
        },
        layers: [{ id: "imagery", type: "raster", source: "imagery" }],
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
    m.setPaintProperty("cells", "fill-color", [
      "interpolate", ["linear"], v, ...RAMP.flat(),
    ] as unknown as maplibregl.ExpressionSpecification);
    m.setPaintProperty("cells", "fill-opacity", [
      "case", ["<", v, 0], 0,
      ["*", p.opacity, ["interpolate", ["linear"], v, 0, 0, 0.15, 0.08, 0.4, 0.4, 1, 0.9]],
    ] as unknown as maplibregl.ExpressionSpecification);
  }, [ready, p.opacity]);

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
      d.textContent = String(k + 1);
      d.title = `Candidate ${k + 1}: score ${c.score.toFixed(2)}`;
      d.onclick = (ev) => {
        ev.stopPropagation();
        cb.current.onCandidate(c);
      };
      return new maplibregl.Marker({ element: d }).setLngLat([c.lon, c.lat]).addTo(m);
    });
  }, [ready, p.candidates]);

  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !p.focus) return;
    m.flyTo({ center: [p.focus.lon, p.focus.lat], zoom: Math.max(m.getZoom(), 11), duration: 900 });
  }, [ready, p.focus]);

  return <div ref={el} className="map" />;
}
