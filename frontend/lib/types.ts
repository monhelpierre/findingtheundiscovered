export type Mode = "near" | "atleast" | "atmost";

export type FeatureSpec = {
  value: number; sigma: number; weight?: number; mode?: Mode; criterion?: number; required?: boolean;
  /** Percentile feature: the target's rank on its own body (0-1). `value` is then Phi^-1(percentile) and
   *  the cell value to compare is in `column` (rel__<feature>: Phi^-1 of the cell's rank on Earth's land). */
  percentile?: number; column?: string;
};

export type Target = {
  id: string;
  name: string;
  body: "moon" | "mars" | "earth";
  category?: string;
  description?: string;
  lat?: number | null;
  lon?: number | null;
  reference_url?: string;
  notes?: string;
  features: Record<string, FeatureSpec>;
  source_file?: string;
};

export type FeatureMeta = { label: string; unit: string; description: string };

export type Tier = "A" | "B" | "C" | "D";

export type Candidate = {
  lat: number; lon: number; score: number; cell: number; region: string;
  /** Target views only: tier, criteria met / scored, and each criterion's score (0-1). */
  tier?: Tier | null; met?: number; scored?: number; criteria?: Record<string, number | null>;
};

export type CriteriaDefs = {
  criteria: Record<string, { short: string; label: string }>;
  applies: Record<string, number[]>;
  feature_criterion: Record<string, number>;
  gates: string[];
  thresholds: { strong: number; met: number; near: number };
  tiers: { min_ratio: number; tier: Tier; label: string }[];
};

export type KnownAnalog = {
  name: string; analog_type: string; bbox: [number, number, number, number]; lat: number; lon: number;
  /** "Moon" or "Mars", and the brief's criteria (1-8) the site is known for. */
  analog_for?: string | null; criteria_known_for?: number[];
};

export type RegionSummary = {
  id: string;
  title: string;
  /** "world": a tile of the worldwide Earth Engine screen (`analog world import`); else a study region. */
  scope?: "study" | "world";
  bbox: [number, number, number, number];
  cell_deg: number;
  layers: string[];
  n_cells: number;
  features: string[];
  excluded_cells?: number;
};

export type ClassifierInfo = {
  classes: string[];
  features: string[];
  regions: string[];
  n_positive: number;
  validation: Record<string, string | number>[];
};

export type Index = {
  generated: string;
  targets: Target[];
  features: Record<string, FeatureMeta>;
  regions: RegionSummary[];
  candidates: Record<string, Candidate[]>;
  /** Worldwide screen: best candidates over all world tiles (only tiles holding one are in `regions`). */
  candidates_world?: Record<string, Candidate[]>;
  world?: { tiles: number; cells: number; published_tiles: number } | null;
  classifier: ClassifierInfo | null;
  known_analogs?: KnownAnalog[];
  exclusion_km?: number;
  /** Set when served by the Django backend: which backend features are available. */
  backend?: { score: boolean; notes: boolean };
} & Partial<CriteriaDefs>;

export type RegionData = {
  id: string;
  title: string;
  bbox: [number, number, number, number];
  cell_deg: number;
  layers: string[];
  n_cells: number;
  columns: Record<string, (number | null)[]>;
};

/** A "view" is what the map is coloured by. */
export type View =
  | { kind: "target"; id: string }
  | { kind: "classifier" }
  | { kind: "class"; cls: string };
