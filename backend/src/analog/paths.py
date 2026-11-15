"""Project paths.

Layout:  <repo>/data/...            downloads, features, models, export
         <repo>/backend/config/...  targets, mineral groups, sources
         <repo>/frontend/           web app (export is copied to frontend/public/data)

Set ANALOG_ROOT to use another project folder; it must contain data/ and config/ (the tests use a
temporary one).
"""

import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]      # <repo>/backend
_override = os.environ.get("ANALOG_ROOT")

ROOT = Path(_override) if _override else BACKEND.parent   # <repo>
DATA = ROOT / "data"
REGIONS = DATA / "regions"
EXPORT = DATA / "export"
MODELS = DATA / "models"
LABELS = DATA / "labels"
CONFIG = ROOT / "config" if _override else BACKEND / "config"
FRONTEND = ROOT / "frontend"
