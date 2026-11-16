"""Low-sun days: Artemis III illumination transposed to Earth, from latitude alone (no data needed).

At the lunar south pole the Sun always stays low on the horizon. On Earth the closest equivalent is
the number of days a year when the Sun rises but its highest (noon) elevation stays under 10 deg.
That only happens at high latitudes: 0 days below ~56 deg, about 107 days at the Arctic Circle.
Same definition as the Moon-base profile of the sister project (NASA Space Apps 2026, Sherbrooke).
"""

from __future__ import annotations

import numpy as np

LOW_SUN_DEG = 10.0


def solar_declination(doy: np.ndarray) -> np.ndarray:
    """Solar declination (deg) for a day of the year (Spencer 1971)."""
    g = 2 * np.pi * (np.asarray(doy, dtype=float) - 1) / 365.0
    d = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
         + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    return np.degrees(d)


_DEC = solar_declination(np.arange(1, 366))


def low_sun_days(lat, low: float = LOW_SUN_DEG) -> np.ndarray:
    """Days per year when the noon Sun is above the horizon but below `low` degrees."""
    lat = np.asarray(lat, dtype=float)
    uniq, inv = np.unique(np.round(lat, 2), return_inverse=True)
    noon = 90.0 - np.abs(uniq[:, None] - _DEC[None, :])  # highest Sun elevation of each day
    days = ((noon > 0) & (noon < low)).sum(axis=1)
    return days[inv].reshape(lat.shape).astype(float)
