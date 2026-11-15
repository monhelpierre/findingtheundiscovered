"""Earth analogs for Moon & Mars base sites."""

import importlib.util
import os
import sys
from pathlib import Path

__version__ = "0.1.0"


def _use_bundled_proj_gdal() -> None:
    """Point PROJ/GDAL at the data bundled with rasterio's wheel.

    Other software (e.g. PostGIS) often sets PROJ_LIB / GDAL_DATA globally to an older PROJ,
    which breaks rasterio with "proj.db ... comes from another PROJ installation".
    Set ANALOG_KEEP_PROJ_ENV=1 to keep your own settings.
    """
    if os.environ.get("ANALOG_KEEP_PROJ_ENV"):
        return
    spec = importlib.util.find_spec("rasterio")
    if not spec or not spec.submodule_search_locations:
        return
    base = Path(list(spec.submodule_search_locations)[0])
    proj = base / "proj_data"
    if proj.is_dir():
        os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = str(proj)
        # rasterio reads the PROJ path when it is imported; if it already was (e.g. a notebook
        # imported rasterio first), point the running PROJ at the bundled data too.
        if "rasterio" in sys.modules:
            try:
                from rasterio._env import set_proj_data_search_path
                set_proj_data_search_path(str(proj))
            except Exception:
                pass
    if (base / "gdal_data").is_dir():
        os.environ["GDAL_DATA"] = str(base / "gdal_data")


_use_bundled_proj_gdal()
