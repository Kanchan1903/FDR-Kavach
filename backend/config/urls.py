from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthcheck(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [path("healthz/", healthcheck, name="healthcheck"), path("api/", include("security.urls"))]
if not settings.DEMO_MODE:
    urlpatterns.insert(1, path("admin/", admin.site.urls))
