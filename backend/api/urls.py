from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("index", views.index),
    path("regions/<str:region_id>", views.region),
    path("score", views.score),
    path("notes", views.notes),
]
