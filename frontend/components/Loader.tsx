/** Loading states: an orbit animation, the full-app splash (skeleton of the layout) and the map overlay. */

export function Orbit({ size = 56 }: { size?: number }) {
  return (
    <span className="orbit" style={{ width: size, height: size }} aria-hidden="true">
      <span className="orbit-planet" />
      <span className="orbit-ring"><span className="orbit-moon" /></span>
    </span>
  );
}

/** Shown while the app code and the exported data load: the layout's skeleton, so nothing jumps after. */
export function AppSplash({ message, detail }: { message: string; detail?: string }) {
  return (
    <div className="app splash" role="status" aria-live="polite">
      <aside className="panel">
        <header className="brand">
          <h1>Earth Analog Explorer</h1>
          <p className="muted">Which places on Earth look like Moon and Mars base sites?</p>
        </header>
        {[0.9, 0.6, 1, 0.75, 0.5, 0.85, 0.7].map((w, i) => (
          <span key={i} className="skeleton" style={{ width: `${w * 100}%`, height: i % 3 === 0 ? 30 : 12 }} />
        ))}
      </aside>
      <div className="resizer" aria-hidden="true" />
      <div className="mapwrap splash-map">
        <div className="loading-card">
          <Orbit size={72} />
          <strong>{message}</strong>
          {detail && <span className="muted small">{detail}</span>}
        </div>
      </div>
    </div>
  );
}

/** Over the map while the base map or a region's cells load; the previous map stays visible underneath. */
export function MapLoading({ message, detail }: { message: string; detail?: string }) {
  return (
    <div className="map-loading" role="status" aria-live="polite">
      <div className="loading-card">
        <Orbit />
        <strong>{message}</strong>
        {detail && <span className="muted small">{detail}</span>}
      </div>
    </div>
  );
}
