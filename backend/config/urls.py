from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from security.demo_accounts import demo_accounts_ready


def healthcheck(_request):
    return JsonResponse(
        {
            "status": "ok",
            "demo_mode": settings.DEMO_MODE,
            "demo_accounts_ready": demo_accounts_ready() if settings.DEMO_MODE else False,
        }
    )


urlpatterns = [path("healthz/", healthcheck, name="healthcheck"), path("api/", include("security.urls"))]
if not settings.DEMO_MODE:
    urlpatterns.insert(1, path("admin/", admin.site.urls))
