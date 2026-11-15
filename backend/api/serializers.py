from rest_framework import serializers

from . import data
from .models import CandidateNote


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateNote
        fields = ["id", "target_id", "region", "cell", "lat", "lon", "author", "text", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_region(self, value):
        if not data.REGION_ID.match(value):
            raise serializers.ValidationError("invalid region id")
        return value

    def validate_author(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("author is required")
        return value

    def validate_text(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("text is required")
        return value
