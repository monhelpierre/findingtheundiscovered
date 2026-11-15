"""Copy targets and known analogs from the export (index.json) into the database, so the team can
edit them in the admin. Existing rows are updated; use --only-new to keep admin edits."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api import data
from api.models import KnownAnalog, Target


class Command(BaseCommand):
    help = "Load targets and known analogs from the exported index.json into the database."

    def add_arguments(self, parser):
        parser.add_argument("--only-new", action="store_true", help="Don't overwrite rows that already exist")

    @transaction.atomic
    def handle(self, *args, only_new=False, **opts):
        try:
            idx = data.index()
        except data.NotExported as e:
            raise CommandError(f"No index.json in {data.export_dir()}: run `analog export` first.") from e

        n_t = n_a = 0
        for order, t in enumerate(idx.get("targets", [])):
            fields = {"name": t["name"], "body": t["body"], "category": t.get("category") or "",
                      "description": t.get("description") or "", "reference_url": t.get("reference_url") or "",
                      "notes": t.get("notes") or "", "lat": t.get("lat"), "lon": t.get("lon"),
                      "features": t["features"], "source_file": t.get("source_file") or "", "order": order}
            if only_new:
                _, created = Target.objects.get_or_create(target_id=t["id"], defaults=fields)
                n_t += created
            else:
                Target.objects.update_or_create(target_id=t["id"], defaults=fields)
                n_t += 1
        for a in idx.get("known_analogs", []):
            w, s, e, n = a["bbox"]
            fields = {"analog_type": a["analog_type"], "analog_for": a.get("analog_for") or "",
                      "criteria_known_for": a.get("criteria_known_for") or [],
                      "west": w, "south": s, "east": e, "north": n}
            if only_new:
                _, created = KnownAnalog.objects.get_or_create(name=a["name"], defaults=fields)
                n_a += created
            else:
                KnownAnalog.objects.update_or_create(name=a["name"], defaults=fields)
                n_a += 1
        self.stdout.write(self.style.SUCCESS(f"Loaded {n_t} target(s) and {n_a} known analog(s)."))
