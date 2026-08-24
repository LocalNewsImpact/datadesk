"""Visuals URLs. The embed lives at the root (/embed/<slug>/), matching
SCOPE.md §2.7; everything else under /visuals/."""

from django.urls import path

from visuals import views

app_name = "visuals"

urlpatterns = [
    path("visuals/", views.index, name="index"),
    path("visuals/builder/new/", views.builder_new, name="builder_new"),
    path(
        "visuals/builder/<slug:slug>/type/",
        views.builder_type,
        name="builder_type",
    ),
    path(
        "visuals/builder/<slug:slug>/step/<str:step>/",
        views.builder_step,
        name="builder_step",
    ),
    path("visuals/builder/<slug:slug>/", views.builder_edit, name="builder_edit"),
    path("visuals/<slug:slug>/", views.page, name="page"),
    path("visuals/<slug:slug>/data.json", views.data_json, name="data"),
    path("embed/<slug:slug>/", views.embed, name="embed"),
]
