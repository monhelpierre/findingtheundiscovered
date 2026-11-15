"""Data the team edits in the admin. Everything else (cells, features, scores) comes from the
pipeline's exported JSON and is read-only here."""

from django.core.exceptions import ValidationError
from django.db import models

MODES = {"near", "atleast", "atmost"}


def validate_features(value):
    """Same format as config/targets.yaml: {feature: {value, sigma, weight?, mode?, required?, criterion?}},
    or {percentile, sigma?, ...} for a percentile feature."""
    if not isinstance(value, dict) or not value:
        raise ValidationError("features must be a non-empty object: {feature: {value, sigma, ...}}")
    for name, spec in value.items():
        if not isinstance(spec, dict):
            raise ValidationError(f"{name}: must be an object")
        if spec.get("percentile") is not None:  # rank on its own body (src/analog/earthref.py)
            p = spec["percentile"]
            if not isinstance(p, (int, float)) or not 0 < p < 1:
                raise ValidationError(f"{name}: 'percentile' must be a number between 0 and 1")
            if not isinstance(spec.get("sigma", 1.0), (int, float)):
                raise ValidationError(f"{name}: 'sigma' must be a number")
        else:
            for key in ("value", "sigma"):
                if not isinstance(spec.get(key), (int, float)):
                    raise ValidationError(f"{name}: '{key}' must be a number")
        if spec.get("sigma", 1.0) <= 0:
            raise ValidationError(f"{name}: sigma must be > 0")
        if not isinstance(spec.get("weight", 1.0), (int, float)) or spec.get("weight", 1.0) < 0:
            raise ValidationError(f"{name}: weight must be a number >= 0")
        if spec.get("mode", "near") not in MODES:
            raise ValidationError(f"{name}: mode must be one of {sorted(MODES)}")


class Target(models.Model):
    """A Moon or Mars target profile. Loaded from the export (`manage.py load_export`); edits here
    override the exported profile in /api/index and are used by /api/score."""

    BODIES = [("moon", "Moon"), ("mars", "Mars"), ("earth", "Earth")]

    target_id = models.SlugField(max_length=64, unique=True, help_text="e.g. moon_malapert_massif")
    name = models.CharField(max_length=200)
    body = models.CharField(max_length=10, choices=BODIES)
    category = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    reference_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    features = models.JSONField(validators=[validate_features])
    source_file = models.CharField(max_length=300, blank=True, help_text="Where the profile first came from")
    active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "target_id"]

    def __str__(self):
        return f"{self.name} ({self.body})"

    def as_dict(self) -> dict:
        return {"id": self.target_id, "name": self.name, "body": self.body, "category": self.category,
                "description": self.description, "lat": self.lat, "lon": self.lon,
                "reference_url": self.reference_url, "notes": self.notes, "features": self.features,
                "source_file": self.source_file or "admin"}


class KnownAnalog(models.Model):
    """A known Earth analog site. Used for the 50 km exclusion: never reported as a discovery."""

    name = models.CharField(max_length=200, unique=True)
    analog_type = models.CharField(max_length=64)
    analog_for = models.CharField(max_length=10, blank=True, help_text="Moon or Mars")
    criteria_known_for = models.JSONField(default=list, blank=True,
                                          help_text="The brief's criteria (1-8) the site is known for, e.g. [1, 6]")
    west = models.FloatField()
    south = models.FloatField()
    east = models.FloatField()
    north = models.FloatField()
    reference = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def clean(self):
        if not (-180 <= self.west < self.east <= 180 and -90 <= self.south < self.north <= 90):
            raise ValidationError("Box must satisfy west < east and south < north, in degrees.")

    def __str__(self):
        return self.name

    def as_dict(self) -> dict:
        return {"name": self.name, "analog_type": self.analog_type, "analog_for": self.analog_for or None,
                "criteria_known_for": self.criteria_known_for or [],
                "bbox": [self.west, self.south, self.east, self.north],
                "lat": round((self.south + self.north) / 2, 4), "lon": round((self.west + self.east) / 2, 4)}


class CandidateNote(models.Model):
    """A team member's note on a cell (e.g. "checked imagery: quarry, not natural")."""

    target_id = models.CharField(max_length=64, blank=True)
    region = models.CharField(max_length=64)
    cell = models.BigIntegerField()
    lat = models.FloatField()
    lon = models.FloatField()
    author = models.CharField(max_length=80)
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["region", "cell"])]

    def __str__(self):
        return f"{self.author} on {self.region}#{self.cell}: {self.text[:40]}"
