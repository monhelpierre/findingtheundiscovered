from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Earth Analog Explorer admin"
admin.site.site_title = "Earth Analog Explorer"

urlpatterns = [
    path("api/admin/", admin.site.urls),
    path("api/", include("api.urls")),
]
