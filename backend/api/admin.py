from django.contrib import admin

from .models import CandidateNote, KnownAnalog, Target


@admin.register(Target)
class TargetAdmin(admin.ModelAdmin):
    list_display = ("name", "target_id", "body", "category", "active", "updated_at")
    list_filter = ("body", "active")
    search_fields = ("name", "target_id", "description")
    list_editable = ("active",)
    readonly_fields = ("updated_at",)
    fieldsets = (
        (None, {"fields": ("target_id", "name", "body", "category", "active", "order")}),
        ("Description", {"fields": ("description", "notes", "reference_url", "lat", "lon", "source_file")}),
        ("Feature profile", {
            "fields": ("features",),
            "description": 'JSON, same format as config/targets.yaml, e.g. '
                           '{"slope_mean": {"value": 5, "sigma": 3, "weight": 1}, '
                           '"ndvi": {"value": 0.1, "sigma": 0.06, "mode": "atmost", "required": true}}',
        }),
        (None, {"fields": ("updated_at",)}),
    )


@admin.register(KnownAnalog)
class KnownAnalogAdmin(admin.ModelAdmin):
    list_display = ("name", "analog_type", "analog_for", "criteria_known_for", "west", "south", "east", "north")
    list_filter = ("analog_type", "analog_for")
    search_fields = ("name", "reference")


@admin.register(CandidateNote)
class CandidateNoteAdmin(admin.ModelAdmin):
    list_display = ("created_at", "author", "region", "cell", "target_id", "text")
    list_filter = ("region", "target_id")
    search_fields = ("author", "text")
