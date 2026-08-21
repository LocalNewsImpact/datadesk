"""Datadesk URL configuration."""

from django.contrib import admin
from django.urls import include, path

from datadesk import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]
