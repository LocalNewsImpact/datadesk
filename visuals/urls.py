"""Visuals URLs. The embed lives at the root (/embed/<slug>/), matching
SCOPE.md §2.6; everything else under /visuals/."""

from django.urls import path

from visuals import views

app_name = "visuals"

urlpatterns = [
    path("visuals/", views.index, name="index"),
    path("visuals/<slug:slug>/", views.page, name="page"),
    path("visuals/<slug:slug>/data.json", views.data_json, name="data"),
    path("embed/<slug:slug>/", views.embed, name="embed"),
]
