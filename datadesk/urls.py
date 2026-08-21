"""Datadesk URL configuration."""

from django.contrib import admin
from django.urls import include, path

from datadesk import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("_health", views.health, name="health"),
    path("explorer/", include("explorer.urls")),
    path("review/", include("review.urls")),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]
