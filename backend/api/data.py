"""Read the pipeline's exported JSON (index.json, regions/<id>.json), with a small in-memory cache
keyed on file modification time, so a re-export is picked up without a restart."""

from __future__ import annotations

import json
import re
import threading
from functools import lru_cache
from pathlib import Path

import pandas as pd
from django.conf import settings

REGION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_lock = threading.RLock()  # re-entrant: region_frame() -> region() -> read_json() nest


class NotExported(Exception):
    pass


def export_dir() -> Path:
    return Path(settings.ANALOG_EXPORT_DIR)


def _path(rel: str) -> Path:
    base = export_dir().resolve()
    p = (base / rel).resolve()
    if base not in p.parents:
        raise NotExported(rel)
    return p


@lru_cache(maxsize=32)
def _read_json(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_json(rel: str) -> dict:
    p = _path(rel)
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError as e:
        raise NotExported(rel) from e
    with _lock:
        return _read_json(str(p), mtime)


def region_path(region_id: str) -> Path:
    if not REGION_ID.match(region_id):
        raise NotExported(region_id)
    p = _path(f"regions/{region_id}.json")
    if not p.is_file():
        raise NotExported(region_id)
    return p


def index() -> dict:
    return read_json("index.json")


def region(region_id: str) -> dict:
    region_path(region_id)
    return read_json(f"regions/{region_id}.json")


@lru_cache(maxsize=16)
def _frame(region_id: str, mtime: float) -> pd.DataFrame:
    cols = region(region_id)["columns"]
    return pd.DataFrame({k: pd.to_numeric(pd.Series(v, dtype="object"), errors="coerce") for k, v in cols.items()})


def region_frame(region_id: str) -> pd.DataFrame:
    """The region's cells as a DataFrame (feature columns as float, missing values as NaN)."""
    mtime = region_path(region_id).stat().st_mtime
    with _lock:
        return _frame(region_id, mtime)
