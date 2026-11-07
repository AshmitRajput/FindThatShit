from django.contrib import admin
from django.urls import include, path, re_path

from config.health import HealthView

urlpatterns = [
    path("admin/", admin.site.urls),
    re_path(r"^api/v1/health/?$", HealthView.as_view(), name="health"),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/", include("resources.urls")),
]
