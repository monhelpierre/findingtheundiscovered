"""API endpoints (all under /api/).

GET  /api/health                     status of the export and the database
GET  /api/index                      index.json, with targets/known analogs from the database if set
GET  /api/regions/<id>               regions/<id>.json (cells, features, exported scores)
GET  /api/score?target=&region=&w.<feature>=<weight>
                                     re-score a region for one target with custom weights
GET  /api/notes?region=&cell=        team notes (newest first)
POST /api/notes                      add a note (header X-Team-Token required)
"""

from __future__ import annotations

import hmac

from django.conf import settings
from django.db import connection
from django.http import FileResponse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import data, scoring
from .models import CandidateNote
from .serializers import NoteSerializer

CACHE_PUBLIC = "public, max-age=300"   # Firebase Hosting's CDN may cache these for 5 minutes
CACHE_NONE = "no-store"


def _cached(resp, value=CACHE_PUBLIC):
    resp["Cache-Control"] = value
    return resp


def _not_exported(what: str):
    return Response({"error": f"{what} not found in the export ({settings.ANALOG_EXPORT_DIR}). "
                              "Run `analog export` (or set ANALOG_EXPORT_DIR)."}, status=404)


@api_view(["GET"])
def health(request):
    try:
        idx = data.index()
        export = {"ok": True, "generated": idx.get("generated"), "regions": len(idx.get("regions", []))}
    except data.NotExported:
        export = {"ok": False}
    try:
        connection.ensure_connection()
        db = {"ok": True, "engine": connection.vendor}
    except Exception as e:  # noqa: BLE001 - report, don't crash the health check
        db = {"ok": False, "error": type(e).__name__}
    ok = export["ok"] and db["ok"]
    return _cached(Response({"ok": ok, "export": export, "database": db},
                            status=200 if ok else 503), CACHE_NONE)


@api_view(["GET"])
def index(request):
    try:
        idx = dict(data.index())
    except data.NotExported:
        return _not_exported("index.json")
    idx["targets"] = scoring.targets()
    idx["known_analogs"] = scoring.known_analogs()
    idx["backend"] = {"score": True, "notes": bool(settings.TEAM_TOKEN)}
    return _cached(Response(idx))


@api_view(["GET"])
def region(request, region_id: str):
    try:
        path = data.region_path(region_id)
    except data.NotExported:
        return _not_exported(f"region '{region_id}'")
    return _cached(FileResponse(open(path, "rb"), content_type="application/json"))


@api_view(["GET"])
def score(request):
    tid, rid = request.query_params.get("target", ""), request.query_params.get("region", "")
    target = scoring.get_target(tid)
    if target is None:
        return Response({"error": f"unknown target '{tid}'"}, status=400)
    try:
        weights = scoring.parse_weights(request.query_params)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    try:
        result = scoring.score_region(rid, scoring.apply_weights(target, weights))
    except data.NotExported:
        return _not_exported(f"region '{rid}'")
    return _cached(Response(result))


def _team_token_ok(request) -> bool:
    token = settings.TEAM_TOKEN
    given = request.headers.get("X-Team-Token", "")
    return bool(token) and hmac.compare_digest(token.encode(), given.encode())


@api_view(["GET", "POST"])
def notes(request):
    if request.method == "POST":
        if not _team_token_ok(request):
            return Response({"error": "missing or wrong X-Team-Token"}, status=status.HTTP_403_FORBIDDEN)
        ser = NoteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return _cached(Response(ser.data, status=201), CACHE_NONE)

    qs = CandidateNote.objects.all()
    if rid := request.query_params.get("region"):
        qs = qs.filter(region=rid)
    if cell := request.query_params.get("cell"):
        if not cell.lstrip("-").isdigit():
            return Response({"error": "cell must be an integer"}, status=400)
        qs = qs.filter(cell=int(cell))
    return _cached(Response(NoteSerializer(qs[:200], many=True).data), CACHE_NONE)
