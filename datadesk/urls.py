"""Datadesk URL configuration."""

from django.contrib import admin
from django.urls import include, path

from datadesk import views

urlpatterns = [
    # Public, and deliberately so: Google requires the page its consent
    # screen links to be reachable without signing in, which every other
    # page of this console is not.
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("", views.landing, name="landing"),
    path("_health", views.health, name="health"),
    path("explorer/", include("explorer.urls")),
    path("review/", include("review.urls")),
    path("manage/", include("datasets.urls")),
    path("", include("visuals.urls")),
    path("manage/", include("accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]
