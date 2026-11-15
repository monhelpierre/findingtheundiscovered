"""A region is a lon/lat bounding box, a cell grid, and a registry of input layers."""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from rasterio.transform import from_origin

from . import paths

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
KIND_RE = re.compile(r"^(dem|albedo|ndvi|temp_range|water|clay_index|ferric_index|mineral:[a-z_]+|custom:[a-z0-9_]+)$")

KIND_HELP = (
    "dem (elevation, m) | albedo (0-1) | ndvi (-1..1) | temp_range (K) | water (1=water, 0=land) | "
    "clay_index (SWIR1/SWIR2) | ferric_index (red/blue) | "
    "mineral:<group> (0/1 or fraction raster) | custom:<feature_name> (averaged per cell)"
)


@dataclass
class Region:
    id: str
    bbox: list[float]  # west, south, east, north (degrees)
    cell_deg: float = 0.01
    title: str = ""
    layers: dict[str, list[str]] = field(default_factory=dict)
    # "study": drawn by the team (default); "world": a tile of the worldwide Earth Engine screen (world.py)
    scope: str = "study"

    # ---- persistence -------------------------------------------------------
    @property
    def dir(self) -> Path:
        return paths.REGIONS / self.id

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "region.json").write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def create(cls, rid: str, bbox, cell_deg: float = 0.01, title: str = "") -> Region:
        if not ID_RE.match(rid):
            raise SystemExit(f"Invalid region id {rid!r}: use lowercase letters, digits, - and _")
        w, s, e, n = map(float, bbox)
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise SystemExit(f"Invalid bbox {bbox}: expected WEST SOUTH EAST NORTH")
        r = cls(id=rid, bbox=[w, s, e, n], cell_deg=float(cell_deg), title=title or rid)
        h, wd = r.shape
        if h * wd > 2_000_000:
            raise SystemExit(f"Grid would have {h * wd:,} cells; use a smaller bbox or larger --cell")
        if (r.dir / "region.json").exists():
            raise SystemExit(f"Region {rid!r} already exists at {r.dir}")
        r.save()
        return r

    @classmethod
    def load(cls, rid: str) -> Region:
        p = paths.REGIONS / rid / "region.json"
        if not p.exists():
            raise SystemExit(f"No region {rid!r}. Create it with: analog region create {rid} --bbox W S E N")
        return cls(**json.loads(p.read_text()))

    @staticmethod
    def all() -> list[Region]:
        return [Region.load(p.parent.name) for p in sorted(paths.REGIONS.glob("*/region.json"))]

    # ---- grid --------------------------------------------------------------
    @property
    def shape(self) -> tuple[int, int]:
        w, s, e, n = self.bbox
        return (
            math.ceil(round((n - s) / self.cell_deg, 6)),
            math.ceil(round((e - w) / self.cell_deg, 6)),
        )

    @property
    def transform(self):
        return from_origin(self.bbox[0], self.bbox[3], self.cell_deg, self.cell_deg)

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        h, w = self.shape
        lon = self.bbox[0] + (np.arange(w) + 0.5) * self.cell_deg
        lat = self.bbox[3] - (np.arange(h) + 0.5) * self.cell_deg
        return np.meshgrid(lon, lat)

    # ---- layers ------------------------------------------------------------
    def resolve(self, p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else self.dir / pp

    def layer_paths(self, kind: str) -> list[Path]:
        return [self.resolve(p) for p in self.layers.get(kind, [])]

    def add_layer(self, kind: str, src, copy: bool = True) -> Path:
        if not KIND_RE.match(kind):
            raise SystemExit(f"Unknown layer kind {kind!r}. Valid: {KIND_HELP}")
        src = Path(src).resolve()
        if not src.exists():
            raise SystemExit(f"File not found: {src}")
        layer_dir = (self.dir / "layers").resolve()
        if src.is_relative_to(self.dir.resolve()):
            stored = src.relative_to(self.dir.resolve()).as_posix()
            dest = src
        elif copy:
            layer_dir.mkdir(parents=True, exist_ok=True)
            safe = kind.replace(":", "_")
            dest = layer_dir / f"{safe}__{src.name}"
            shutil.copy2(src, dest)
            stored = dest.relative_to(self.dir.resolve()).as_posix()
        else:
            dest = src
            stored = str(src)
        lst = self.layers.setdefault(kind, [])
        if stored not in lst:
            lst.append(stored)
        self.save()
        return dest

    def remove_layer(self, kind: str) -> None:
        self.layers.pop(kind, None)
        self.save()
